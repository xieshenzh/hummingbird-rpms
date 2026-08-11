#!/usr/bin/env python3
"""Helper utilities for HUM CVE ticket triage.

This script centralizes repetitive /cve skill ticket-gathering work so the
agent can consume a compact, structured summary instead of repeatedly
executing and parsing long command outputs in-chat.

Subcommands:
  show (default)  Summarize HUM CVE ticket(s)
  bot-mrs         List open/merged automation bot MRs for a package
  sbom            Fetch package SBOM (Jira attachment or Pulp) and search it
  spec-deps       Probe a package .spec for a component / bundled Provides
  worktree        Create an isolated git worktree for a HUM task ticket
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,8}")
HUM_RE = re.compile(r"HUM-\d{3,6}")
MR_URL_RE = re.compile(
    r"https://gitlab\.com/redhat/hummingbird/rpms/-/merge_requests/\d+"
)
BOT_USER = "project_73447720_bot_6f7c574289c710ebc9ab9ee76059d959"
PULP_SBOM_BASE = (
    "https://packages.redhat.com/api/pulp-content/public-hummingbird/"
    "metadata/sboms"
)
GITLAB_RPMS_REPO = "redhat/hummingbird/rpms"
TRANSIENT_RHJIRA_RE = re.compile(
    r"proxy|tunnel|timed out|timeout|temporar|502|503|504|connection reset|\beof\b",
    re.IGNORECASE,
)
COMMANDS = ("show", "bot-mrs", "sbom", "spec-deps", "worktree")
SBOM_FILE_RE = re.compile(r"sha256-[a-fA-F0-9]+\.sbom")
BUNDLED_PROVIDES_RE = re.compile(
    r"(?i)^\s*Provides:\s*bundled\(([^)]+)\)(?:\s*=\s*(\S+))?"
)
JIRA_FIELDS_FULL = ",".join(
    [
        "summary",
        "status",
        "issuetype",
        "assignee",
        "labels",
        "description",
        "comment",
        "issuelinks",
        "customfield_10578",  # Fixed in Build
        "customfield_10632",  # Upstream Affected Component
        "customfield_10840",  # Severity
    ]
)
JIRA_FIELDS_LINKED = "summary,status,issuetype"
JIRA_FIELDS_BATCH = "summary,status,issuetype,assignee,labels"
_json_cache: dict[str, dict[str, Any]] = {}

### Output formatting ###

TICKET_HEADER_FMT = "{ticket}{pkg} {ttype} {status} | {sev} | {fib} | {assignee}{cves}"
TICKET_DETAIL = {
    "assessment": "  Assessment: {0}",
    "affected": "  Affected: {0}",
    "fixed": "  Fixed: {0}",
    "mr": "  MR: {0}",
    "link": "  Link: {ticket} [{ttype}] {status} - {summary}",
    "link_error": "  Link: {ticket} ERROR {error}",
    "component": "  Upstream component: {0}",
    "vendored": "  Vendored deps: yes (go-vendor-tools)",
}

### Dataclasses ###


@dataclass
class LinkedTicket:
    ticket: str
    summary: str = ""
    status: str = ""
    ticket_type: str = ""
    error: str = ""


@dataclass(init=False)
class TicketReport:
    ticket: str
    summary: str
    status: str
    severity: str
    assignee: str
    ticket_type: str
    labels: str
    fixed_in_build: str
    cve_ids: list[str]
    package_guess: str
    linked_keys: list[str]
    mr_links_in_ticket: list[str]
    linked_ticket_details: list[LinkedTicket]
    embargoed: bool
    assessment: str
    affected_range: str
    fixed_version: str
    cve_analysis_block: str
    description: str
    upstream_component: str

    def __init__(self, *args, **kwargs):
        if args:
            key, summary, status, issuetype, assignee, labels = args
            kwargs = dict(
                ticket=key,
                summary=summary,
                status=status,
                severity="",
                assignee=assignee,
                ticket_type=issuetype,
                labels=labels,
                fixed_in_build="",
                linked_keys=[],
                mr_links_in_ticket=[],
                linked_ticket_details=[],
                cve_ids=sorted(set(CVE_RE.findall(summary))),
                package_guess=extract_package_guess(summary, labels, ""),
                embargoed=summary.upper().startswith("EMBARGOED"),
                assessment="",
                affected_range="",
                fixed_version="",
                cve_analysis_block="",
                description="",
                upstream_component="",
            )
        self.__dict__.update(kwargs)


### Utilities ###


def repo_root() -> Path:
    # 4 parents up: .cursor/skills/cve → .cursor/skills → .cursor → repo root
    # (.claude/skills/cve is hardlinked to the same path depth)
    return Path(__file__).resolve().parent.parent.parent.parent


def run_command(
    args: list[str],
    *,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, check=False, capture_output=True, text=True, cwd=cwd
        )
    except OSError as err:
        raise RuntimeError(f"Failed to execute {' '.join(args)}: {err}") from err


def run_rhjira(
    args: list[str],
    *,
    retries: int = 3,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    """Run rhjira with bounded retries for transient proxy/network failures."""
    backoff = 2
    last: subprocess.CompletedProcess | None = None
    for attempt in range(1, retries + 1):
        last = run_command(["rhjira", *args], cwd=cwd)
        if last.returncode == 0:
            return last
        combined = f"{last.stderr or ''}\n{last.stdout or ''}"
        if attempt == retries or not TRANSIENT_RHJIRA_RE.search(combined):
            return last
        print(
            f"WARN: transient Jira/proxy error (attempt {attempt}/{retries}); "
            f"retrying in {backoff}s",
            file=sys.stderr,
        )
        time.sleep(backoff)
        backoff += 2
    assert last is not None
    return last


def uses_vendored_deps(package: str) -> bool:
    go_vendor_path = repo_root() / "rpms" / package / "go-vendor-tools.toml"
    return go_vendor_path.exists()


def normalize_hum_key(value: str) -> str:
    trimmed = value.strip().upper()
    if trimmed.startswith("HUM-"):
        return trimmed
    if re.fullmatch(r"\d+", trimmed):
        return f"HUM-{trimmed}"
    raise ValueError(f"Invalid ticket key: {value}")


def pulp_sbom_package_dir(package: str) -> str:
    """Pulp SBOM dirs replace dots in the package name with hyphens."""
    return f"{package.replace('.', '-')}-main"


def _json_str(data: dict[str, Any], *path: str, default: str = "") -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return str(current) if current is not None else default


def find_related_tickets(initial_cve_ids: list[str]) -> list[str]:
    if not initial_cve_ids:
        return []

    or_clauses = " OR ".join(f'text ~ "{c}"' for c in initial_cve_ids)
    jql = f'project = HUM AND status not in (Closed, "CLOSED (invalid)", Done, Resolved, "Won\'t Fix") AND ({or_clauses})'
    result = run_rhjira(
        ["list", jql, "--fields", "key", "--rawoutput", "--noheader"]
    )
    if result.returncode != 0:
        print(
            f"Warning: Failed to search for related tickets: {result.stderr}",
            file=sys.stderr,
        )
        return []

    all_tickets: set[str] = set()
    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            candidate = parts[1].strip()
            if HUM_RE.fullmatch(candidate):
                all_tickets.add(candidate)
    return sorted(all_tickets)


def ensure_rhjira_available() -> None:
    if shutil.which("rhjira"):
        return
    print("WARNING: `rhjira` is not installed or not found in PATH.", file=sys.stderr)
    print("Install it with:", file=sys.stderr)
    print("  pip install rhjira", file=sys.stderr)
    print("Then configure authentication with:", file=sys.stderr)
    print("  rhjira settoken", file=sys.stderr)
    sys.exit(1)


def ensure_glab_available() -> None:
    if shutil.which("glab"):
        return
    raise RuntimeError("`glab` is not installed or not found in PATH.")


### Extraction ###


def _extract_assignee(fields: dict[str, Any]) -> str:
    assignee = _json_str(fields, "assignee", "displayName")
    if not assignee:
        assignee = _json_str(fields, "assignee", "emailAddress")
    return assignee


def _extract_labels(fields: dict[str, Any]) -> str:
    raw = fields.get("labels")
    return ",".join(raw) if isinstance(raw, list) else ""


def extract_assessment(block: str) -> str:
    m = re.search(r"^ASSESSMENT:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_vendor_field(block: str, field: str) -> str:
    m = re.search(rf"^\s{{2}}{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_flaw_summary(description: str) -> str:
    m = re.search(r"Flaw:\s*\n-+\s*\n(.*?)(?:\n~~~|\Z)", description, re.DOTALL)
    return m.group(1).strip() if m else ""


def extract_cve_analysis_block(text: str) -> str:
    # NOTE: This heuristic may pick a comment {noformat} block instead of the
    # canonical cve_analysis block if comments include matching keywords.
    noformat_blocks = re.findall(
        r"\{noformat\}(.*?)\{noformat\}", text, flags=re.DOTALL
    )
    for block in reversed(noformat_blocks):
        lowered = block.lower()
        if "hummingbird srpm version" in lowered or "affected version range" in lowered:
            return block.strip()
        if "assessment:" in lowered and "next steps:" in lowered and "cve-" in lowered:
            return block.strip()
    return ""


def extract_package_guess(summary: str, labels: str, fixed_in_build: str) -> str:
    # Common summary form: "CVE-2026-12345 pkgname: ..."
    summary_match = re.search(
        r"CVE-\d{4}-\d{4,8}\s+([A-Za-z0-9+_.-]+)\s*:",
        summary,
    )
    if summary_match:
        return summary_match.group(1)

    # Labels frequently include pscomponent:<package>
    labels_match = re.search(r"pscomponent:([A-Za-z0-9+_.-]+)", labels)
    if labels_match:
        return labels_match.group(1)

    # Fixed in Build often starts with "<name>-<version>-..."
    if fixed_in_build:
        # Keep package names like "nodejs25" and "python3.13"
        fib_match = re.match(r"([A-Za-z0-9+_.-]+)-\d", fixed_in_build)
        if fib_match:
            return fib_match.group(1)

    return ""


def _extract_linked_keys(fields: dict[str, Any]) -> list[str]:
    links = fields.get("issuelinks")
    if not links:
        return []
    keys: list[str] = []
    for link in links:
        for direction in ("inwardIssue", "outwardIssue"):
            issue = link.get(direction)
            if issue and issue.get("key"):
                keys.append(issue["key"])
    return list(dict.fromkeys(keys))


### Parsing ###


def _comment_bodies(fields: dict[str, Any]) -> list[str]:
    comment_data = fields.get("comment")
    if not comment_data or not isinstance(comment_data, dict):
        return []
    return [
        c["body"]
        for c in comment_data.get("comments", [])
        if isinstance(c, dict) and c.get("body")
    ]


def parse_ticket_json(ticket_key: str, fields: dict[str, Any]) -> TicketReport:
    # Extract standard Jira fields (summary, status, assignee, severity, FIB)
    summary = fields.get("summary", "")
    status = _json_str(fields, "status", "name")
    ticket_type = _json_str(fields, "issuetype", "name")
    assignee = _extract_assignee(fields)
    severity = _json_str(fields, "customfield_10840", "value")
    fixed_in_build = fields.get("customfield_10578") or ""
    if isinstance(fixed_in_build, dict):
        fixed_in_build = fixed_in_build.get("value", "")

    labels = _extract_labels(fields)
    embargoed = summary.upper().startswith("EMBARGOED")

    upstream_component = fields.get("customfield_10632") or ""
    description = fields.get("description") or ""
    bodies = _comment_bodies(fields)
    searchable_text = "\n".join([summary, description] + bodies)

    # Extract derived fields (linked keys, CVE IDs, MR links, cve_analysis)
    linked_keys = _extract_linked_keys(fields)

    cve_ids = sorted(set(CVE_RE.findall(searchable_text)))
    mr_links = sorted(set(MR_URL_RE.findall(searchable_text)))

    cve_analysis = extract_cve_analysis_block(searchable_text)
    assessment = extract_assessment(cve_analysis) if cve_analysis else ""
    affected_range = (
        extract_vendor_field(cve_analysis, "Affected") if cve_analysis else ""
    )
    fixed_version = (
        extract_vendor_field(cve_analysis, "Fixed in") if cve_analysis else ""
    )

    package_guess = extract_package_guess(summary, labels, str(fixed_in_build))

    return TicketReport(
        ticket=ticket_key,
        summary=summary,
        status=status,
        severity=severity,
        assignee=assignee,
        ticket_type=ticket_type,
        labels=labels,
        fixed_in_build=str(fixed_in_build),
        cve_ids=cve_ids,
        package_guess=package_guess,
        linked_keys=linked_keys,
        mr_links_in_ticket=mr_links,
        linked_ticket_details=[],
        embargoed=embargoed,
        assessment=assessment,
        affected_range=affected_range,
        fixed_version=fixed_version,
        cve_analysis_block=cve_analysis,
        description=description,
        upstream_component=upstream_component,
    )


### Fetch ###


def fetch_json(
    key: str,
    fields: str = JIRA_FIELDS_FULL,
    *,
    raise_on_error: bool = True,
) -> dict[str, Any] | None:
    cache_key = f"{key}:{fields}"
    if cache_key in _json_cache:
        return _json_cache[cache_key]
    result = run_rhjira(["dump", key, "--fields", fields, "--json"])
    if result.returncode != 0:
        if raise_on_error:
            raise RuntimeError(
                f"rhjira dump {key} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        if raise_on_error:
            raise RuntimeError(f"Invalid JSON from rhjira dump {key}: {err}") from err
        return None
    parsed = data.get("fields") or {}
    if not parsed:
        if raise_on_error:
            raise RuntimeError(f"rhjira dump {key} returned no 'fields' data")
        return None
    _json_cache[cache_key] = parsed
    return parsed


def batch_fetch_linked(keys: list[str]) -> dict[str, LinkedTicket]:
    if not keys:
        return {}
    result: dict[str, LinkedTicket] = {}
    for key in keys:
        fields = fetch_json(key, JIRA_FIELDS_LINKED, raise_on_error=False)
        if fields is None:
            continue
        result[key] = LinkedTicket(
            ticket=key,
            summary=fields.get("summary", ""),
            status=_json_str(fields, "status", "name"),
            ticket_type=_json_str(fields, "issuetype", "name"),
        )
    return result


def batch_fetch_tickets(keys: list[str]) -> dict[str, TicketReport]:
    if not keys:
        return {}
    result: dict[str, TicketReport] = {}
    for key in keys:
        fields = fetch_json(key, JIRA_FIELDS_BATCH, raise_on_error=False)
        if fields is None:
            continue

        summary = fields.get("summary", "")
        status = _json_str(fields, "status", "name")
        issuetype = _json_str(fields, "issuetype", "name")
        result[key] = TicketReport(
            key,
            summary,
            status,
            issuetype,
            _extract_assignee(fields),
            _extract_labels(fields),
        )
    return result


def inspect_linked_tickets(
    current_ticket: str,
    linked_keys: list[str],
    max_linked: int,
) -> list[LinkedTicket]:
    keys_to_fetch = [k for k in linked_keys if k != current_ticket][:max_linked]
    if not keys_to_fetch:
        return []

    fetched = batch_fetch_linked(keys_to_fetch)
    return [
        fetched.get(key, LinkedTicket(ticket=key, error="not found in batch fetch"))
        for key in keys_to_fetch
    ]


### Output ###


def print_ticket(report: TicketReport) -> None:
    if report.embargoed:
        print(f"*** EMBARGOED: {report.ticket} — do not analyze or act. ***")
        return

    # Header line
    print(
        TICKET_HEADER_FMT.format(
            ticket=report.ticket,
            pkg=f" [{report.package_guess}]" if report.package_guess else "",
            ttype=report.ticket_type or "?",
            status=report.status,
            sev=f"Sev:{report.severity}" if report.severity else "Sev:-",
            fib=f"FIB:{report.fixed_in_build}" if report.fixed_in_build else "FIB:-",
            assignee=report.assignee or "Unassigned",
            cves=f" | {','.join(report.cve_ids)}" if report.cve_ids else "",
        )
    )
    # Detail lines
    if report.assessment:
        print(TICKET_DETAIL["assessment"].format(report.assessment))
    if report.affected_range or report.fixed_version:
        parts = []
        if report.affected_range:
            parts.append(TICKET_DETAIL["affected"].format(report.affected_range))
        if report.fixed_version:
            parts.append(TICKET_DETAIL["fixed"].format(report.fixed_version))
        print("  ".join(parts))
    for mr in report.mr_links_in_ticket:
        print(TICKET_DETAIL["mr"].format(mr))
    for linked in report.linked_ticket_details:
        if linked.error:
            print(
                TICKET_DETAIL["link_error"].format(
                    ticket=linked.ticket, error=linked.error
                )
            )
        else:
            print(
                TICKET_DETAIL["link"].format(
                    ticket=linked.ticket,
                    ttype=linked.ticket_type,
                    status=linked.status,
                    summary=linked.summary,
                )
            )
    if report.upstream_component:
        print(TICKET_DETAIL["component"].format(report.upstream_component))
    if report.package_guess and uses_vendored_deps(report.package_guess):
        print(TICKET_DETAIL["vendored"])
    if report.description:
        if flaw := _extract_flaw_summary(report.description):
            print()
            print(flaw)
    if report.cve_analysis_block:
        print()
        print(report.cve_analysis_block)


def build_suggested_chat_title(reports: list[TicketReport]) -> str:
    if not reports:
        return ""
    tickets = [r.ticket for r in reports]
    packages = [r.package_guess for r in reports]
    if len(reports) == 1:
        return f"{tickets[0]} {packages[0]}" if packages[0] else tickets[0]
    if all(packages) and len(set(packages)) == 1:
        return f"{' '.join(tickets)} {packages[0]}"
    return ", ".join(f"{t} {p}" if p else t for t, p in zip(tickets, packages))


def apply_title_prefix(title: str, prefix: str) -> str:
    return " ".join(filter(None, [prefix.strip(), title.strip()]))


### Probe helpers (bot-mrs / sbom / spec-deps / worktree) ###


@dataclass
class BotMrEntry:
    iid: str
    title: str
    state: str
    web_url: str = ""


@dataclass
class SbomHit:
    term: str
    path: str
    name: str = ""
    version: str = ""
    purl: str = ""
    scope: str = ""
    component_type: str = ""
    snippet: str = ""


@dataclass
class SpecDepHit:
    kind: str
    line_no: int
    text: str
    bundled_name: str = ""
    bundled_version: str = ""


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


def http_get_text(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hummingbird-cve-helper"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code} fetching {url}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Failed fetching {url}: {err}") from err


def http_download(url: str, dest: Path, timeout: float = 120.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "hummingbird-cve-helper"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.write_bytes(resp.read())
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code} downloading {url}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Failed downloading {url}: {err}") from err


def latest_pulp_sbom_filename(package: str) -> tuple[str, str]:
    """Return (directory_url, filename) for the latest Pulp SBOM object."""
    directory = f"{PULP_SBOM_BASE}/{pulp_sbom_package_dir(package)}/"
    listing = http_get_text(directory)
    files = sorted(set(SBOM_FILE_RE.findall(listing)))
    if not files:
        raise RuntimeError(f"No SBOM files found under {directory}")
    return directory, files[-1]


def try_download_jira_sbom(ticket: str, nvr: str, dest: Path) -> bool:
    """Download `{nvr}.sbom.json` from a Jira ticket attachment into dest."""
    ensure_rhjira_available()
    attachment = f"{nvr}.sbom.json"
    # rhjira writes the attachment into cwd using the attachment name
    workdir = dest.parent
    workdir.mkdir(parents=True, exist_ok=True)
    staged = workdir / attachment
    if staged.exists():
        staged.unlink()
    result = run_rhjira(["attach", "-d", ticket, attachment], cwd=workdir)
    if result.returncode != 0 or not staged.exists():
        return False
    if staged.resolve() != dest.resolve():
        dest.write_bytes(staged.read_bytes())
        if staged.exists() and staged.resolve() != dest.resolve():
            staged.unlink()
    return dest.exists()


def fetch_sbom(
    package: str,
    *,
    nvr: str = "",
    ticket: str = "",
    dest: Path | None = None,
) -> dict[str, Any]:
    """Fetch SBOM preferring a matching Jira attachment, else Pulp."""
    out = dest or Path(f"/tmp/{package}.sbom.json")
    out = out.expanduser()
    source = ""
    url = ""

    if ticket and nvr:
        ticket_key = normalize_hum_key(ticket)
        if try_download_jira_sbom(ticket_key, nvr, out):
            source = "jira"
            url = f"jira:{ticket_key}/{nvr}.sbom.json"
        else:
            print(
                f"WARN: Jira attachment {nvr}.sbom.json not found on {ticket_key}; "
                "falling back to Pulp",
                file=sys.stderr,
            )

    if not source:
        directory, filename = latest_pulp_sbom_filename(package)
        url = f"{directory}{filename}"
        http_download(url, out)
        source = "pulp"

    return {
        "package": package,
        "nvr": nvr,
        "ticket": ticket,
        "source": source,
        "url": url,
        "path": str(out),
        "bytes": out.stat().st_size if out.exists() else 0,
    }


def _component_fields(node: dict[str, Any]) -> dict[str, str]:
    name = str(node.get("name") or "")
    version = str(node.get("version") or "")
    purl = str(node.get("purl") or "")
    scope = str(node.get("scope") or "")
    ctype = str(node.get("type") or "")
    props = node.get("properties")
    if isinstance(props, list) and not scope:
        for prop in props:
            if not isinstance(prop, dict):
                continue
            key = str(prop.get("name") or prop.get("key") or "").lower()
            if "scope" in key or key.endswith("lifecycle"):
                scope = str(prop.get("value") or "")
                break
    return {
        "name": name,
        "version": version,
        "purl": purl,
        "scope": scope,
        "component_type": ctype,
    }


def search_sbom_file(path: Path, terms: list[str]) -> list[SbomHit]:
    """Search an SBOM for terms; prefer structured component hits, else text."""
    if not terms:
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[SbomHit] = []
    seen: set[tuple[str, str, str, str]] = set()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    def consider(term: str, node: dict[str, Any], json_path: str) -> None:
        fields = _component_fields(node)
        blob = " ".join(fields.values()).lower()
        if term.lower() not in blob and term.lower() not in json.dumps(node).lower():
            return
        key = (term, fields["name"], fields["version"], fields["purl"])
        if key in seen:
            return
        seen.add(key)
        hits.append(
            SbomHit(
                term=term,
                path=json_path,
                name=fields["name"],
                version=fields["version"],
                purl=fields["purl"],
                scope=fields["scope"],
                component_type=fields["component_type"],
                snippet="",
            )
        )

    def walk(node: Any, json_path: str = "$") -> None:
        if isinstance(node, dict):
            # CycloneDX component-like object
            if "name" in node or "purl" in node:
                for term in terms:
                    consider(term, node, json_path)
            for key, value in node.items():
                walk(value, f"{json_path}.{key}")
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, f"{json_path}[{idx}]")

    if data is not None:
        walk(data)

    if hits:
        return hits

    # Text fallback (non-JSON or no structured component matches)
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        for match in pattern.finditer(text):
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            snippet = text[start:end].replace("\n", " ")
            hits.append(
                SbomHit(
                    term=term,
                    path="text",
                    snippet=snippet,
                )
            )
            if sum(1 for h in hits if h.term == term) >= 10:
                break
    return hits


def print_sbom_result(
    meta: dict[str, Any],
    hits: list[SbomHit],
    terms: list[str],
) -> None:
    print(f"SBOM package: {meta['package']}")
    print(f"Source: {meta['source']}")
    print(f"URL: {meta['url']}")
    print(f"Path: {meta['path']} ({meta['bytes']} bytes)")
    if meta.get("nvr"):
        print(f"NVR: {meta['nvr']}")
    if not terms:
        return
    print(f"\nSearch terms: {', '.join(terms)}")
    print(f"Hits: {len(hits)}")
    if not hits:
        print("(no matches)")
        return
    for hit in hits:
        label = hit.name or hit.purl or hit.path
        bits = [f"term={hit.term}", f"match={label}"]
        if hit.version:
            bits.append(f"version={hit.version}")
        if hit.scope:
            bits.append(f"scope={hit.scope}")
        if hit.component_type:
            bits.append(f"type={hit.component_type}")
        if hit.purl and hit.name:
            bits.append(f"purl={hit.purl}")
        print("- " + " | ".join(bits))
        if hit.snippet:
            print(f"  snippet: {hit.snippet}")


def probe_spec_deps(package: str, component: str = "") -> list[SpecDepHit]:
    spec_path = repo_root() / "rpms" / package / f"{package}.spec"
    if not spec_path.is_file():
        raise RuntimeError(f"Spec not found: {spec_path}")
    hits: list[SpecDepHit] = []
    lines = spec_path.read_text(encoding="utf-8", errors="replace").splitlines()
    component_re = re.compile(re.escape(component), re.IGNORECASE) if component else None
    for idx, line in enumerate(lines, start=1):
        bundled = BUNDLED_PROVIDES_RE.search(line)
        if bundled:
            name, version = bundled.group(1), bundled.group(2) or ""
            if component_re is None or component_re.search(name) or component_re.search(line):
                hits.append(
                    SpecDepHit(
                        kind="bundled_provides",
                        line_no=idx,
                        text=line.strip(),
                        bundled_name=name,
                        bundled_version=version,
                    )
                )
                continue
        if component_re and component_re.search(line):
            hits.append(
                SpecDepHit(
                    kind="component_mention",
                    line_no=idx,
                    text=line.strip(),
                )
            )
    return hits


def print_spec_deps(package: str, component: str, hits: list[SpecDepHit]) -> None:
    spec_path = repo_root() / "rpms" / package / f"{package}.spec"
    print(f"Spec: {spec_path}")
    if component:
        print(f"Component: {component}")
    print(f"Hits: {len(hits)}")
    if not hits:
        print("(no matches)")
        return
    for hit in hits:
        extra = ""
        if hit.kind == "bundled_provides":
            extra = f" bundled={hit.bundled_name}"
            if hit.bundled_version:
                extra += f"={hit.bundled_version}"
        print(f"{hit.line_no}:{hit.kind}{extra}: {hit.text}")


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
    remote_check = run_command(["git", "rev-parse", "--verify", f"origin/{base}"], cwd=root)
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


### CLI ###


def _add_show_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "tickets",
        nargs="+",
        help="HUM ticket key(s), e.g. HUM-3120 or 3120. Related tickets with the same CVEs are discovered automatically.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output instead of human-readable summary.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write full JSON output to file (does not imply --json for stdout).",
    )
    parser.add_argument(
        "--max-linked",
        type=int,
        default=8,
        help="Maximum linked tickets to inspect per ticket (default: 8).",
    )
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="Print only deterministic suggested chat title.",
    )
    parser.add_argument(
        "--title-prefix",
        default="",
        help="Optional prefix to prepend to suggested chat title (e.g. 'FIB' or '!1234').",
    )
    parser.add_argument(
        "--no-find-related",
        action="store_false",
        dest="find_related",
        default=True,
        help=(
            "Skip automatic discovery of related tickets with the same CVE IDs. "
            "By default, related tickets are included to enable batch resolution "
            "in a single invocation."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HUM CVE helper for /cve workflow probes and ticket summaries.",
    )
    subparsers = parser.add_subparsers(dest="command")

    show = subparsers.add_parser(
        "show",
        help="Summarize HUM CVE ticket details (default command).",
    )
    _add_show_arguments(show)

    bot = subparsers.add_parser(
        "bot-mrs",
        help="List open and merged automation bot MRs for a package.",
    )
    bot.add_argument("package", help="Package name to search in bot MR titles/branches.")
    bot.add_argument(
        "--per-page",
        type=int,
        default=5,
        help="Max MRs per state (default: 5).",
    )
    bot.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text.",
    )

    sbom = subparsers.add_parser(
        "sbom",
        help="Fetch package SBOM (Jira attachment preferred, else Pulp) and optionally search it.",
    )
    sbom.add_argument("package", help="Package name (Hummingbird SRPM name).")
    sbom.add_argument(
        "--nvr",
        default="",
        help="NVR used to locate a matching Jira {nvr}.sbom.json attachment.",
    )
    sbom.add_argument(
        "--ticket",
        default="",
        help="HUM ticket that may have an attached SBOM (used with --nvr).",
    )
    sbom.add_argument(
        "--search",
        action="append",
        default=[],
        help="Component/module search term (repeatable).",
    )
    sbom.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination path (default: /tmp/<package>.sbom.json).",
    )
    sbom.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text.",
    )

    spec = subparsers.add_parser(
        "spec-deps",
        help="Probe rpms/<pkg>/<pkg>.spec for bundled Provides and component mentions.",
    )
    spec.add_argument("package", help="Package name.")
    spec.add_argument(
        "--component",
        default="",
        help="Component/library name to search for (optional; lists all bundled Provides if omitted).",
    )
    spec.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text.",
    )

    worktree = subparsers.add_parser(
        "worktree",
        help="Create ../worktrees/HUM-YYYY worktree for isolated CVE fix work.",
    )
    worktree.add_argument("ticket", help="HUM task ticket key (e.g. HUM-5936 or 5936).")
    worktree.add_argument(
        "--base",
        default="main",
        help="Base branch/ref (default: main; uses origin/main when available).",
    )
    worktree.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text.",
    )
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """Allow bare `cve_helper.py HUM-1234` by inserting the default `show` command."""
    if not argv:
        return ["show", "--help"]
    if argv[0] in ("-h", "--help"):
        return argv
    if argv[0] in COMMANDS:
        return argv
    return ["show", *argv]


def cmd_show(args: argparse.Namespace) -> int:
    if args.max_linked < 0:
        print("--max-linked must be >= 0", file=sys.stderr)
        return 2

    ensure_rhjira_available()

    initial_keys: list[str] = [normalize_hum_key(key) for key in args.tickets]
    ticket_keys: list[str] = list(initial_keys)
    initial_set = set(initial_keys)

    # Full JSON fetch for user-provided tickets (need custom fields, comments)
    initial_reports: dict[str, TicketReport] = {}
    for key in initial_keys:
        try:
            fields = fetch_json(key)
        except RuntimeError as e:
            print(f"Warning: failed to fetch {key}: {e}", file=sys.stderr)
            continue
        print(f"Processing {key}...", file=sys.stderr, end="\r")
        initial_reports[key] = parse_ticket_json(key, fields)  # type: ignore[arg-type] - Can't be None, fetch_json would raise

    if args.find_related:
        print("Finding related tickets...", file=sys.stderr)
        initial_cve_ids: set[str] = set()
        for report in initial_reports.values():
            initial_cve_ids.update(report.cve_ids)

        if initial_cve_ids:
            related_keys = find_related_tickets(list(initial_cve_ids))
            new_keys = set(related_keys) - initial_set
            ticket_keys = list(dict.fromkeys(ticket_keys + related_keys))
            print(
                f"Found {len(ticket_keys)} total tickets ({len(new_keys)} newly discovered)\n",
                file=sys.stderr,
            )
        else:
            print(
                "Warning: Could not extract any CVE IDs from initial tickets; "
                "--find-related had no effect.",
                file=sys.stderr,
            )

    reports: list[TicketReport] = []

    # Batch fetch discovered tickets
    discovered_keys = [k for k in ticket_keys if k not in initial_set]
    discovered_reports: dict[str, TicketReport] = {}
    if discovered_keys:
        print(
            f"Batch-fetching {len(discovered_keys)} discovered tickets...",
            file=sys.stderr,
        )
        discovered_reports = batch_fetch_tickets(discovered_keys)

    # Merge and attach linked ticket details
    all_reports = {**initial_reports, **discovered_reports}

    for key, parsed in all_reports.items():
        parsed.linked_ticket_details = inspect_linked_tickets(
            current_ticket=key,
            linked_keys=parsed.linked_keys,
            max_linked=args.max_linked,
        )
        reports.append(parsed)

    suggested_chat_title = build_suggested_chat_title(reports)
    suggested_chat_title = apply_title_prefix(suggested_chat_title, args.title_prefix)
    payload: dict[str, Any] = {
        "suggested_chat_title": suggested_chat_title,
        "tickets": [asdict(r) for r in reports],
    }

    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    if args.title_only:
        print(suggested_chat_title)
    elif args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for idx, report in enumerate(reports):
            if idx:
                print()
            print_ticket(report)
        print(f"\nSuggested chat title: {suggested_chat_title}")

    return 0


def cmd_bot_mrs(args: argparse.Namespace) -> int:
    mrs = list_bot_mrs(args.package, per_page=args.per_page)
    if args.json:
        print(
            json.dumps(
                {
                    "package": args.package,
                    "bot_user": BOT_USER,
                    "open": [asdict(x) for x in mrs["open"]],
                    "merged": [asdict(x) for x in mrs["merged"]],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_bot_mrs(args.package, mrs)
    return 0


def cmd_sbom(args: argparse.Namespace) -> int:
    meta = fetch_sbom(
        args.package,
        nvr=args.nvr,
        ticket=args.ticket,
        dest=args.out,
    )
    hits = search_sbom_file(Path(meta["path"]), args.search)
    if args.json:
        print(
            json.dumps(
                {
                    **meta,
                    "search": args.search,
                    "hits": [asdict(h) for h in hits],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_sbom_result(meta, hits, args.search)
    return 0


def cmd_spec_deps(args: argparse.Namespace) -> int:
    hits = probe_spec_deps(args.package, args.component)
    if args.json:
        print(
            json.dumps(
                {
                    "package": args.package,
                    "component": args.component,
                    "spec": str(repo_root() / "rpms" / args.package / f"{args.package}.spec"),
                    "hits": [asdict(h) for h in hits],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print_spec_deps(args.package, args.component, hits)
    return 0


def cmd_worktree(args: argparse.Namespace) -> int:
    dest = create_task_worktree(args.ticket, base=args.base)
    payload = {
        "ticket": normalize_hum_key(args.ticket),
        "path": str(dest),
        "branch": normalize_hum_key(args.ticket),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"WORKTREE={dest}")
        print(f"BRANCH={payload['branch']}")
        print("Move the agent workspace into this worktree before editing files.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = normalize_argv(list(argv) if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    handlers = {
        "show": cmd_show,
        "bot-mrs": cmd_bot_mrs,
        "sbom": cmd_sbom,
        "spec-deps": cmd_spec_deps,
        "worktree": cmd_worktree,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as err:
        print(f"Input error: {err}", file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as err:
        print(f"Runtime error: {err}", file=sys.stderr)
        raise SystemExit(1)
