import os
import re
import shutil
from typing import Callable
from datetime import datetime, timezone
from pathlib import Path

from lib.models import BotMrEntry, MR_URL_RE, GITLAB_RPMS_REPO
from lib.utils import run_command, http_get_json, normalize_hum_key, repo_root


# ---- Constants ----

BOT_USER = "project_73447720_bot_6f7c574289c710ebc9ab9ee76059d959"
GITHUB_COMMIT_RE = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+)/commit/([0-9a-fA-F]{7,40})"
)


# ---- glab availability ----


def ensure_glab_available() -> None:
    """Raise if the `glab`/`lab` CLI is not on PATH.

    open_package_mr() below assumes glab/lab >= 0.25.1, which uses positional
    `mr create [target_remote [target_branch]]` and `mr note [remote] <id>`
    syntax instead of the older --source-branch/--target-branch/--head/--repo
    flags. If a future CLI version changes this syntax again, the "unknown
    flag" errors will surface here first.
    """
    if shutil.which("glab"):
        return
    raise RuntimeError("`glab` is not installed or not found in PATH.")


# ---- GitLab bot MR listing ----


def _parse_glab_mr_list(output: str, state: str) -> list[BotMrEntry]:
    entries: list[BotMrEntry] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("no merge"):
            continue
        # Typical: !1234  Title here  (branch) ← or similar
        m = re.match(r"!(\d+)\s+(.*)$", line)
        if not m:
            continue
        iid = m.group(1)
        rest = m.group(2).strip()
        # Drop trailing metadata like "(branch)" when present at end
        title = re.sub(r"\s+\([^)]*\)\s*$", "", rest).strip() or rest
        entries.append(
            BotMrEntry(
                iid=iid,
                title=title,
                state=state,
                web_url=(
                    f"https://gitlab.com/{GITLAB_RPMS_REPO}/-/merge_requests/{iid}"
                ),
            )
        )
    return entries


def list_bot_mrs(package: str, *, per_page: int = 5) -> dict[str, list[BotMrEntry]]:
    ensure_glab_available()
    result: dict[str, list[BotMrEntry]] = {"open": [], "merged": []}
    for state, flag in (("open", []), ("merged", ["--merged"])):
        cmd = [
            "glab",
            "mr",
            "list",
            "--repo",
            GITLAB_RPMS_REPO,
            "--author",
            BOT_USER,
            "--search",
            package,
            "--per-page",
            str(per_page),
            *flag,
        ]
        proc = run_command(cmd)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"glab mr list ({state}) failed: {err}")
        result[state] = _parse_glab_mr_list(proc.stdout, state)
    return result


def print_bot_mrs(package: str, mrs: dict[str, list[BotMrEntry]]) -> None:
    print(f"Bot MRs for package: {package}")
    print(f"Bot user: {BOT_USER}")
    for state in ("open", "merged"):
        entries = mrs.get(state, [])
        print(f"\n=== {state.upper()} ({len(entries)}) ===")
        if not entries:
            print("(none)")
            continue
        for entry in entries:
            print(f"!{entry.iid}  {entry.title}")
            print(f"  {entry.web_url}")


# ---- GitHub API helpers ----


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_github_commit_ref(value: str) -> tuple[str, str, str] | None:
    """Return (owner, repo, sha) from a GitHub commit URL, else None."""
    match = GITHUB_COMMIT_RE.search(value or "")
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def parse_iso_datetime(value: str) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ValueError("empty datetime")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compare_fix_age(commit_dt: datetime, tag_dt: datetime) -> dict[str, str]:
    """Compare a fix commit timestamp to an upstream tag/release timestamp."""
    if commit_dt <= tag_dt:
        return {
            "verdict": "commit_at_or_before_tag",
            "hint": "shipped tag may include the fix; agent decides Done-Errata",
        }
    return {
        "verdict": "commit_after_tag",
        "hint": "shipped tag likely missing the fix; do not set FIB from the tag alone",
    }


def fetch_github_commit_date(owner: str, repo: str, sha: str) -> datetime:
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
    data = http_get_json(url, extra_headers=_github_headers())
    date = (
        ((data.get("commit") or {}).get("committer") or {}).get("date")
        or ((data.get("commit") or {}).get("author") or {}).get("date")
        or ""
    )
    if not date:
        raise RuntimeError(f"GitHub commit {owner}/{repo}@{sha} has no date")
    return parse_iso_datetime(str(date))


def fetch_github_tag_date(owner: str, repo: str, tag: str) -> datetime:
    tag_name = tag.removeprefix("refs/tags/")
    release_url = (
        f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag_name}"
    )
    try:
        release = http_get_json(release_url, extra_headers=_github_headers())
        published = release.get("published_at") or release.get("created_at") or ""
        if published:
            return parse_iso_datetime(str(published))
    except RuntimeError as e:
        if "404" not in str(e) and "Not Found" not in str(e):
            raise
    ref_url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{tag_name}"
    ref = http_get_json(ref_url, extra_headers=_github_headers())
    obj = ref.get("object") or {}
    obj_sha = obj.get("sha") or ""
    obj_type = obj.get("type") or ""
    if obj_type == "tag":
        tag_obj = http_get_json(
            f"https://api.github.com/repos/{owner}/{repo}/git/tags/{obj_sha}",
            extra_headers=_github_headers(),
        )
        date = (tag_obj.get("tagger") or {}).get("date") or ""
        if date:
            return parse_iso_datetime(str(date))
    if obj_sha:
        return fetch_github_commit_date(owner, repo, str(obj_sha))
    raise RuntimeError(f"GitHub tag {owner}/{repo} {tag_name} has no date")


def resolve_fix_age_dates(
    *,
    commit: str = "",
    tag: str = "",
    repo: str = "",
    commit_date: str = "",
    tag_date: str = "",
) -> tuple[datetime, datetime, dict[str, str]]:
    """Resolve commit/tag timestamps from flags, URLs, or the GitHub API."""
    meta = {
        "commit": commit,
        "tag": tag,
        "repo": repo,
        "commit_date_source": "",
        "tag_date_source": "",
    }
    commit_dt: datetime | None = None
    tag_dt: datetime | None = None
    if commit_date:
        commit_dt = parse_iso_datetime(commit_date)
        meta["commit_date_source"] = "flag"
    if tag_date:
        tag_dt = parse_iso_datetime(tag_date)
        meta["tag_date_source"] = "flag"

    gh = parse_github_commit_ref(commit)
    owner = repo_name = sha = ""
    if gh:
        owner, repo_name, sha = gh
        meta["commit"] = sha
        meta["repo"] = f"{owner}/{repo_name}"
    elif repo:
        parts = repo.rstrip("/").removesuffix(".git")
        if "github.com/" in parts:
            tail = parts.split("github.com/", 1)[1]
            bits = [p for p in tail.split("/") if p]
            if len(bits) >= 2:
                owner, repo_name = bits[0], bits[1]
        elif "/" in parts:
            owner, repo_name = parts.split("/", 1)
        sha = commit.strip()

    if commit_dt is None:
        if not (owner and repo_name and sha):
            raise ValueError(
                "Need --commit-date, or a GitHub commit URL / --commit SHA with --repo"
            )
        commit_dt = fetch_github_commit_date(owner, repo_name, sha)
        meta["commit_date_source"] = "github"
    if tag_dt is None:
        if not tag:
            raise ValueError("Need --tag-date or --tag")
        if not (owner and repo_name):
            raise ValueError(
                "Need --tag-date, or --tag with a GitHub --repo / commit URL"
            )
        tag_dt = fetch_github_tag_date(owner, repo_name, tag)
        meta["tag_date_source"] = "github"
    return commit_dt, tag_dt, meta


# ---- GitLab project helpers ----


def gitlab_project_from_url(url: str) -> str:
    cleaned = url.strip()
    cleaned = re.sub(r"^git@gitlab\.com:", "https://gitlab.com/", cleaned)
    cleaned = cleaned.removesuffix(".git").rstrip("/")
    if "gitlab.com/" not in cleaned:
        raise RuntimeError(f"Not a gitlab.com remote URL: {url}")
    return cleaned.split("gitlab.com/", 1)[1]


def detect_fork_remote(cwd: Path | str | None = None) -> tuple[str, str]:
    """Return (remote_name, gitlab project path) for the user's fork."""
    proc = run_command(["git", "remote", "-v"], cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "git remote -v failed")
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1] != "(push)":
            continue
        name, url = parts[0], parts[1]
        try:
            project = gitlab_project_from_url(url)
        except RuntimeError:
            continue
        if project.rstrip("/") == GITLAB_RPMS_REPO:
            continue
        return name, project
    raise RuntimeError(
        "Could not detect a fork remote. Check `git remote -v` "
        f"(need a push remote other than {GITLAB_RPMS_REPO})."
    )


def detect_target_remote(cwd: Path | str | None = None) -> str:
    """Return the remote name that points at GITLAB_RPMS_REPO (e.g. "origin")."""
    proc = run_command(["git", "remote", "-v"], cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or "git remote -v failed")
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[-1] != "(push)":
            continue
        name, url = parts[0], parts[1]
        try:
            project = gitlab_project_from_url(url)
        except RuntimeError:
            continue
        if project.rstrip("/") == GITLAB_RPMS_REPO.rstrip("/"):
            return name
    raise RuntimeError(
        f"Could not detect a remote pointing at {GITLAB_RPMS_REPO}. Check `git remote -v`."
    )


# ---- Git worktree ----


def create_task_worktree(ticket: str, *, base: str = "main") -> Path:
    ticket_key = normalize_hum_key(ticket)
    root = repo_root()
    worktrees = (root / ".." / "worktrees").resolve()
    worktrees.mkdir(parents=True, exist_ok=True)
    dest = worktrees / ticket_key
    if dest.exists():
        raise RuntimeError(f"Worktree path already exists: {dest}")
    # Prefer origin/<base> when available
    ref = base
    remote_check = run_command(
        ["git", "rev-parse", "--verify", f"origin/{base}"], cwd=root
    )
    if remote_check.returncode == 0:
        ref = f"origin/{base}"
    proc = run_command(
        ["git", "worktree", "add", str(dest), "-b", ticket_key, ref],
        cwd=root,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git worktree add failed: {err}")
    return dest


# ---- Lookaside upload helper ----


def lookaside_upload_commands(files: list[Path], package: str) -> list[str]:
    cmds: list[str] = []
    for src in files:
        staged = Path("/tmp") / src.name
        cmds.append(f"cp {src} {staged}")
        cmds.append(f"./ci/upload-to-lookaside-cache.sh -f {staged} -p {package}")
    return cmds


# ---- MR creation ----


def build_mr_description(
    summary: str,
    *,
    task: str,
    trackers: list[str],
    cves: list[str],
) -> str:
    tracker_list = ", ".join(normalize_hum_key(t) for t in trackers)
    cve_list = ", ".join(cves)
    return (
        f"{summary.rstrip()}\n\n"
        f"Closes: {normalize_hum_key(task)}\n"
        f"Ref: {tracker_list}\n"
        f"CVE: {cve_list}\n"
    )


def open_package_mr(
    *,
    title: str,
    description: str,
    task: str,
    source_branch: str,
    post_comment: Callable[[str, str], bool],
    cwd: Path | str | None = None,
    push: bool = True,
    trigger_review: bool = True,
) -> str:
    ensure_glab_available()
    fork_remote, _fork_project = detect_fork_remote(cwd=cwd)
    target_remote = detect_target_remote(cwd=cwd)
    if push:
        pushed = run_command(["git", "push", "-u", fork_remote, source_branch], cwd=cwd)
        if pushed.returncode != 0:
            raise RuntimeError(
                (pushed.stderr or pushed.stdout or "git push failed").strip()
            )
    created = run_command(
        [
            "glab",
            "mr",
            "create",
            target_remote,
            "main",
            "--source",
            f"{fork_remote}:{source_branch}",
            "-m",
            title,
            "-m",
            description,
            "--no-edit",
        ],
        cwd=cwd,
    )
    if created.returncode != 0:
        raise RuntimeError(
            (created.stderr or created.stdout or "glab mr create failed").strip()
        )
    urls = MR_URL_RE.findall(f"{created.stdout}\n{created.stderr}")
    if not urls:
        # glab may print a short /merge_requests/N path
        tail = (created.stdout or created.stderr or "").strip().splitlines()
        last = tail[-1] if tail else ""
        if last.startswith("http"):
            mr_url = last.strip()
        else:
            raise RuntimeError(
                f"Could not parse MR URL from glab output:\n{created.stdout}"
            )
    else:
        mr_url = urls[-1]
    post_comment(normalize_hum_key(task), f"MR: {mr_url}\n")
    if trigger_review:
        iid = mr_url.rstrip("/").rsplit("/", 1)[-1]
        run_command(
            [
                "glab",
                "mr",
                "note",
                target_remote,
                iid,
                "-m",
                "/hummingbird code-review",
            ],
            cwd=cwd,
        )
    return mr_url
