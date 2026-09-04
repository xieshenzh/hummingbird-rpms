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
  deps            Check Jira auth and hummingbird_cve_analysis import
  comment         Post a Jira comment via jira_client
                  (--kind nab|fib|analysis|next-release fills wiki markup)
  next-release    Comment + cve-next-release label, leave In Progress
  set-fib         Set Fixed in Build after a Pulp NVR check
  close-nab       Comment, set VEX, close as Not a Bug
  create-task     Create a HUM Task, link blockers, set In Progress
  open-mr         Push the fork and open a package fix MR
  lookaside-cmd   Print lookaside upload commands; do not upload
  investigate     One-shot show + probes + related-ticket recap
  version-check   Compare local NVR vs CVE range / FIB / analysis NVR
  upstream-fix-age  Compare fix-commit date vs upstream tag/release date
  release-bump    Print the next spec .N from metadata + spec (no writes)

Jira writes import hummingbird_cve_analysis.lib (jira_client, pulp). Auth
is JIRA_TOKEN plus JIRA_URL or rhjira's JIRA_SERVER / JIRA_EMAIL from
~/.config/rhjira/agent.env — no extra token is required if rhjira works.
"""

import argparse
import json
import sys
import urllib.error
from dataclasses import asdict
from pathlib import Path
from typing import Any

# ---- Skill dir bootstrap ----

_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from cve_analysis_bridge import (  # noqa: E402
    AnalysisImportError,
    jira_client_module,
    load_jira_auth,
)

# ---- lib imports ----

from lib.utils import (  # noqa: E402
    _pkg_dir,
    normalize_hum_key,
)
from lib.jira import (  # noqa: E402
    apply_next_release,
    close_not_a_bug,
    create_hum_task,
    post_comment,
    set_fixed_in_build,
)
from lib.sbom import (  # noqa: E402
    fetch_sbom,
    search_sbom_file,
)
from lib.spec import (  # noqa: E402
    extract_analysis_nvr,
    probe_spec_deps,
    print_spec_deps,
    release_bump_payload,
    apply_release_bump,
    version_check_payload,
)
from lib.vcs import (  # noqa: E402
    BOT_USER,
    build_mr_description,
    compare_fix_age,
    create_task_worktree,
    list_bot_mrs,
    lookaside_upload_commands,
    open_package_mr,
    print_bot_mrs,
    resolve_fix_age_dates,
)
from lib.output import (  # noqa: E402
    apply_title_prefix,
    build_suggested_chat_title,
    print_sbom_result,
    print_ticket,
)
from lib.investigate import (  # noqa: E402
    gather_ticket_reports,
    log_message,
    recap_lines,
    resolution_hint_for_report,
    search_repo_fix_mrs,
    strip_only_keyword,
    ticket_has_task_or_mr,
)
from lib.log import export_agent_log  # noqa: E402
from lib.comments import (  # noqa: E402
    _add_comment_template_arguments,
    _emit_comment_body,
    _skip_jira_write,
    resolve_comment_body,
)


# ---- CLI commands list ----

COMMANDS = (
    "show",
    "bot-mrs",
    "sbom",
    "spec-deps",
    "worktree",
    "deps",
    "comment",
    "next-release",
    "set-fib",
    "close-nab",
    "create-task",
    "open-mr",
    "lookaside-cmd",
    "investigate",
    "version-check",
    "upstream-fix-age",
    "release-bump",
    "log-message",
)


### CLI ###


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HUM CVE helper for /cve workflow probes and ticket summaries.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ---- Shared parent parsers ----

    _json_p = argparse.ArgumentParser(add_help=False)
    _json_p.add_argument(
        "--json", action="store_true", help="Print JSON instead of text."
    )

    _msg_p = argparse.ArgumentParser(add_help=False)
    _msg_p.add_argument(
        "-m",
        "--message",
        default="",
        help="Comment body, or extra notes when --kind is set.",
    )
    _msg_p.add_argument(
        "-f",
        "--file",
        type=Path,
        dest="comment_file",
        help="Read body from file (overrides --kind).",
    )

    _show_p = argparse.ArgumentParser(add_help=False)
    _show_p.add_argument(
        "tickets", nargs="+", help="HUM ticket key(s), e.g. HUM-3120 or 3120."
    )
    _show_p.add_argument("--json", action="store_true", help="Print JSON output.")
    _show_p.add_argument("--json-out", type=Path, help="Write JSON output to file.")
    _show_p.add_argument(
        "--max-linked",
        type=int,
        default=8,
        help="Max linked tickets to inspect (default: 8).",
    )
    _show_p.add_argument(
        "--title-only", action="store_true", help="Print only suggested chat title."
    )
    _show_p.add_argument(
        "--title-prefix",
        default="",
        help="Prefix to prepend to chat title (e.g. 'FIB' or '!1234').",
    )
    _show_p.add_argument(
        "--no-find-related",
        action="store_false",
        dest="find_related",
        default=True,
        help="Skip automatic discovery of related tickets with the same CVE IDs.",
    )

    # ---- Subcommands ----

    subparsers.add_parser(
        "show",
        parents=[_show_p],
        help="Summarize HUM CVE ticket details (default command).",
    )

    bot = subparsers.add_parser(
        "bot-mrs",
        parents=[_json_p],
        help="List open and merged automation bot MRs for a package.",
    )
    bot.add_argument(
        "package", help="Package name to search in bot MR titles/branches."
    )
    bot.add_argument(
        "--per-page", type=int, default=5, help="Max MRs per state (default: 5)."
    )

    sbom = subparsers.add_parser(
        "sbom",
        parents=[_json_p],
        help="Fetch package SBOM (Jira attachment preferred, else Pulp) and optionally search it.",
    )
    sbom.add_argument("package", help="Package name (Hummingbird SRPM name).")
    sbom.add_argument("--nvr", default="", help="NVR for Jira attachment lookup.")
    sbom.add_argument(
        "--ticket", default="", help="HUM ticket with attached SBOM (used with --nvr)."
    )
    sbom.add_argument(
        "--search",
        action="append",
        default=[],
        help="Component search term (repeatable).",
    )
    sbom.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination path (default: /tmp/<package>.sbom.json).",
    )

    spec = subparsers.add_parser(
        "spec-deps",
        parents=[_json_p],
        help="Probe rpms/<pkg>/<pkg>.spec for bundled Provides and component mentions.",
    )
    spec.add_argument("package", help="Package name.")
    spec.add_argument(
        "--component",
        default="",
        help="Component to search for (lists all bundled Provides if omitted).",
    )

    worktree = subparsers.add_parser(
        "worktree",
        parents=[_json_p],
        help="Create ../worktrees/HUM-YYYY worktree for isolated CVE fix work.",
    )
    worktree.add_argument("ticket", help="HUM task ticket key (e.g. HUM-5936 or 5936).")
    worktree.add_argument(
        "--base",
        default="main",
        help="Base branch/ref (default: main; uses origin/main when available).",
    )

    subparsers.add_parser(
        "deps",
        parents=[_json_p],
        help="Check Jira auth mapping and hummingbird_cve_analysis import.",
    )

    comment = subparsers.add_parser(
        "comment",
        parents=[_msg_p],
        help="Post a Jira comment via hummingbird_cve_analysis.lib.jira_client.",
    )
    comment.add_argument(
        "tickets",
        nargs="*",
        help="HUM ticket key(s). Optional with --print-only / --json.",
    )
    _add_comment_template_arguments(comment)

    nxt = subparsers.add_parser(
        "next-release",
        parents=[_msg_p],
        help="Comment, add cve-next-release, remove cve-needs-attention.",
    )
    nxt.add_argument(
        "tickets",
        nargs="*",
        help="HUM ticket key(s). Optional with --print-only / --json.",
    )
    _add_comment_template_arguments(nxt)

    fib = subparsers.add_parser(
        "set-fib", help="Set Fixed in Build after verifying the NVR exists in Pulp."
    )
    fib.add_argument("ticket", help="HUM CVE tracker key.")
    fib.add_argument("nvr", help="NVR or foo-1.2-3.src.rpm")
    fib.add_argument(
        "--package",
        required=True,
        help="SRPM package name used for the Pulp listing (e.g. grafana13.1).",
    )
    fib.add_argument(
        "--force",
        action="store_true",
        help="Set FIB even if Pulp does not list the NVR (user override).",
    )

    nab = subparsers.add_parser(
        "close-nab",
        parents=[_msg_p],
        help="Comment, set VEX, and close as Not a Bug. Ask the user first.",
    )
    nab.add_argument(
        "tickets",
        nargs="*",
        help="HUM ticket key(s). Optional with --print-only / --json.",
    )
    nab.add_argument(
        "--vex",
        required=True,
        choices=("Component not Present", "Vulnerable Code not Present"),
        help="VEX justification.",
    )
    _add_comment_template_arguments(nab, with_vex=False)

    task = subparsers.add_parser(
        "create-task",
        help="Create a HUM Task, link CVE trackers with --blocks, set In Progress.",
    )
    task.add_argument("--summary", required=True, help="Task summary.")
    task.add_argument(
        "--blocks",
        action="append",
        default=[],
        help="CVE tracker this task blocks (repeatable).",
    )
    task.add_argument(
        "--assignee", default="", help="Assignee email (default: JIRA_EMAIL)."
    )
    task.add_argument(
        "--components",
        default="A2: CVE & Scanners",
        help="Jira component (default: A2: CVE & Scanners).",
    )
    task.add_argument(
        "--worktree",
        action="store_true",
        help="Also create ../worktrees/<task> after the ticket exists.",
    )
    task.add_argument("--base", default="main", help="Worktree base branch.")

    mr = subparsers.add_parser(
        "open-mr",
        help="Push the fork branch and create a package fix MR (not advisory_handler).",
    )
    mr.add_argument("--task", required=True, help="HUM task ticket to Closes.")
    mr.add_argument(
        "--tracker",
        action="append",
        default=[],
        dest="trackers",
        help="CVE tracker keys for Ref: (repeatable).",
    )
    mr.add_argument(
        "--cve",
        action="append",
        default=[],
        dest="cves",
        help="CVE IDs for the CVE: trailer (repeatable).",
    )
    mr.add_argument("--title", required=True, help="MR title.")
    mr.add_argument(
        "-m",
        "--message",
        default="",
        help="MR summary paragraph (trailers are appended).",
    )
    mr.add_argument(
        "-f", "--file", type=Path, dest="description_file", help="MR summary from file."
    )
    mr.add_argument(
        "--source-branch", default="", help="Branch to push (default: the task key)."
    )
    mr.add_argument("--no-push", action="store_true", help="Skip git push.")
    mr.add_argument(
        "--no-review",
        action="store_true",
        help="Do not post /hummingbird code-review on the MR.",
    )

    lookaside = subparsers.add_parser(
        "lookaside-cmd", help="Print lookaside copy/upload commands; do not upload."
    )
    lookaside.add_argument(
        "-f",
        "--file",
        action="append",
        dest="files",
        required=True,
        type=Path,
        help="Artifact to stage (repeatable).",
    )
    lookaside.add_argument(
        "-p",
        "--package",
        required=True,
        help="RPM package name (e.g. grafana13.1, not grafana13).",
    )

    inv = subparsers.add_parser(
        "investigate",
        parents=[_show_p],
        help="One-shot ticket recap plus bot-mrs, spec-deps, and GitLab MR search.",
    )
    inv.add_argument(
        "--only",
        action="store_true",
        help="Skip find-related (same as a trailing `only` token).",
    )
    inv.add_argument(
        "--sbom", action="store_true", help="Also fetch and search the package SBOM."
    )

    vchk = subparsers.add_parser(
        "version-check",
        parents=[_json_p],
        help="Compare local spec NVR vs CVE range / FIB / analysis NVR.",
    )
    vchk.add_argument("package", help="SRPM package name.")
    vchk.add_argument(
        "--ticket", default="", help="HUM tracker to read FIB/range from."
    )
    vchk.add_argument("--nvr", default="", help="Override local NVR.")
    vchk.add_argument("--fib", default="", help="Fixed in Build NVR override.")
    vchk.add_argument("--affected", default="", help="CVE affected range override.")
    vchk.add_argument("--fixed", default="", help="CVE fixed version override.")
    vchk.add_argument(
        "--analysis-nvr",
        default="",
        dest="analysis_nvr",
        help="cve_analysis NVR override.",
    )

    age = subparsers.add_parser(
        "upstream-fix-age",
        parents=[_json_p],
        help="Compare a fix commit date against an upstream tag/release date.",
    )
    age.add_argument("--commit", default="", help="Commit SHA or GitHub commit URL.")
    age.add_argument("--tag", default="", help="Upstream tag or release name.")
    age.add_argument(
        "--repo", default="", help="GitHub owner/repo (or URL) when --commit is a SHA."
    )
    age.add_argument(
        "--commit-date",
        default="",
        dest="commit_date",
        help="ISO-8601 commit timestamp (skips GitHub fetch).",
    )
    age.add_argument(
        "--tag-date",
        default="",
        dest="tag_date",
        help="ISO-8601 tag/release timestamp (skips GitHub fetch).",
    )

    rbump = subparsers.add_parser(
        "release-bump",
        parents=[_json_p],
        help="Print or apply the next spec .N from metadata + spec.",
    )
    rbump.add_argument("package", help="SRPM package name.")
    rbump.add_argument(
        "--apply",
        action="store_true",
        help="Write the bumped Release: line into rpms/<pkg>/<pkg>.spec.",
    )

    log_cmd = subparsers.add_parser(
        "log-message",
        help="Attach a log or transcript file to a Jira ticket.",
    )
    log_cmd.add_argument("ticket", help="HUM ticket key (e.g. HUM-6378).")
    log_cmd.add_argument(
        "file",
        type=Path,
        nargs="?",
        default=None,
        help="Path to log file (e.g. /path/to/transcript.md). Omit and use "
        "--opencode/--claude/--cursor to auto-export a session instead.",
    )
    agent_group = log_cmd.add_mutually_exclusive_group()
    agent_group.add_argument(
        "--opencode",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help="Export an OpenCode session transcript from "
        "~/.local/share/opencode/opencode.db and attach that instead of "
        "FILE. Defaults to the most recently updated session; pass a "
        "specific `ses_...` id to target another one.",
    )
    agent_group.add_argument(
        "--claude",
        nargs="?",
        const="",
        default=None,
        metavar="JSONL_PATH",
        help="Export a Claude Code session transcript from "
        "~/.claude/projects/<cwd>/*.jsonl and attach that instead of FILE. "
        "Defaults to the most recently modified session for the current "
        "directory; pass an explicit .jsonl path to target another one.",
    )
    agent_group.add_argument(
        "--cursor",
        nargs="?",
        const="",
        default=None,
        metavar="COMPOSER_ID",
        help="Export a Cursor chat/composer session and attach that instead "
        "of FILE. Best-effort: searches desktop and cursor-server User dirs, "
        "then ~/.cursor/projects/<slug>/agent-transcripts/, preferring the "
        "newer source. Pass a composer id or agent-transcript uuid to select "
        "a specific session.",
    )
    log_cmd.add_argument(
        "--print-only",
        action="store_true",
        help="Check log file without attaching to Jira.",
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

    tickets, only = strip_only_keyword(list(args.tickets))
    find_related = args.find_related and not only
    gathered = gather_ticket_reports(
        tickets, find_related=find_related, max_linked=args.max_linked
    )
    reports = gathered.reports

    suggested_chat_title = build_suggested_chat_title(reports)
    suggested_chat_title = apply_title_prefix(suggested_chat_title, args.title_prefix)
    payload: dict[str, Any] = {
        "suggested_chat_title": suggested_chat_title,
        "tickets": [asdict(r) for r in reports],
        "user_provided": gathered.user_provided,
        "discovered": gathered.discovered,
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
                    "spec": str(_pkg_dir(args.package) / f"{args.package}.spec"),
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
    ticket_key = normalize_hum_key(args.ticket)
    payload = {
        "ticket": ticket_key,
        "path": str(dest),
        "branch": ticket_key,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"WORKTREE={dest}")
        print(f"BRANCH={payload['branch']}")
        print("Move the agent workspace into this worktree before editing files.")
    return 0


def cmd_deps(args: argparse.Namespace) -> int:
    """Report Jira auth source and whether hummingbird_cve_analysis imports."""
    auth_ok = True
    auth_error = ""
    auth_source = ""
    auth_url = ""
    auth_user = ""
    try:
        auth = load_jira_auth()
        auth_source = auth.source
        auth_url = auth.base_url
        auth_user = auth.basic_auth_user or ""
    except RuntimeError as err:
        auth_ok = False
        auth_error = str(err)

    analysis_ok = True
    analysis_error = ""
    try:
        jira_client_module()
    except AnalysisImportError as err:
        analysis_ok = False
        analysis_error = str(err)

    payload = {
        "jira_auth_ok": auth_ok,
        "jira_auth_source": auth_source,
        "jira_url": auth_url,
        "jira_user": auth_user,
        "jira_auth_error": auth_error,
        "hummingbird_cve_analysis_ok": analysis_ok,
        "hummingbird_cve_analysis_error": analysis_error,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        auth_state = "ok" if auth_ok else f"missing ({auth_error})"
        extra = ""
        if auth_ok:
            extra = f" url={auth_url}"
            if auth_user:
                extra += f" user={auth_user}"
            extra += f" source={auth_source}"
        print(f"jira_auth: {auth_state}{extra}")
        if analysis_ok:
            print("hummingbird_cve_analysis: ok")
        else:
            print(f"hummingbird_cve_analysis: missing ({analysis_error})")
    return 0 if auth_ok and analysis_ok else 1


def cmd_comment(args: argparse.Namespace) -> int:
    body = resolve_comment_body(args)
    kind = str(getattr(args, "kind", "") or "")
    if _skip_jira_write(args):
        _emit_comment_body(args, body, kind)
        return 0
    tickets = list(args.tickets or [])
    if not tickets:
        raise RuntimeError("Pass HUM ticket key(s), or --print-only / --json")
    for raw in tickets:
        ticket = normalize_hum_key(raw)
        posted = post_comment(ticket, body)
        print(
            f"{ticket}: {'comment posted' if posted else 'duplicate comment skipped'}"
        )
    return 0


def cmd_next_release(args: argparse.Namespace) -> int:
    body = resolve_comment_body(args, default_kind="next-release")
    kind = str(getattr(args, "kind", "") or "") or "next-release"
    if _skip_jira_write(args):
        _emit_comment_body(args, body, kind)
        return 0
    tickets = list(args.tickets or [])
    if not tickets:
        raise RuntimeError("Pass HUM ticket key(s), or --print-only / --json")
    for raw in tickets:
        ticket = normalize_hum_key(raw)
        apply_next_release(ticket, body)
        print(f"{ticket}: cve-next-release; left In Progress")
    return 0


def cmd_set_fib(args: argparse.Namespace) -> int:
    ticket = normalize_hum_key(args.ticket)
    detail = set_fixed_in_build(ticket, args.nvr, args.package, force=args.force)
    print(f"{ticket}: Fixed in Build set ({detail})")
    return 0


def _has_nab_evidence(args: argparse.Namespace) -> bool:
    """Return True when SBOM or spec-deps evidence is present on the CLI."""
    sbom = str(getattr(args, "sbom_match", "") or "").strip().lower()
    spec = str(getattr(args, "spec_deps", "") or "").strip().lower()
    if sbom and sbom != "no hits":
        return True
    if spec and spec != "no spec-deps hits":
        return True
    return False


def cmd_close_nab(args: argparse.Namespace) -> int:
    body = resolve_comment_body(args, default_kind="nab")
    kind = str(getattr(args, "kind", "") or "") or "nab"
    if _skip_jira_write(args):
        _emit_comment_body(args, body, kind)
        return 0

    tickets = list(args.tickets or [])
    if not tickets:
        raise RuntimeError("Pass HUM ticket key(s), or --print-only / --json")

    # ---- Evidence gate (before any Jira write) ----
    if not _has_nab_evidence(args):
        raise RuntimeError(
            "Refusing close-nab: no SBOM or spec-deps evidence. "
            "Search the SBOM for the ticket's Upstream Affected Component "
            "(customfield_10632) and pass --sbom-match / --spec-deps."
        )

    for raw in tickets:
        ticket = normalize_hum_key(raw)
        close_not_a_bug(ticket, body, args.vex)
        print(f"{ticket}: closed Not a Bug ({args.vex})")
    return 0


def cmd_create_task(args: argparse.Namespace) -> int:
    if not args.blocks:
        raise ValueError("Pass at least one --blocks HUM-XXXX CVE tracker")
    key = create_hum_task(
        args.summary,
        blocks=args.blocks,
        assignee=args.assignee,
        components=args.components,
    )
    print(f"TASK={key}")
    if args.worktree:
        dest = create_task_worktree(key, base=args.base)
        print(f"WORKTREE={dest}")
        print("Move the agent workspace into this worktree before editing files.")
    return 0


def cmd_open_mr(args: argparse.Namespace) -> int:
    if not args.trackers:
        raise ValueError("Pass at least one --tracker HUM-XXXX")
    if not args.cves:
        raise ValueError("Pass at least one --cve CVE-YYYY-NNNNN")
    summary = args.message
    if args.description_file is not None:
        summary = args.description_file.read_text(encoding="utf-8")
    if not summary.strip():
        raise ValueError("Provide --message or --file with the MR summary")
    task = normalize_hum_key(args.task)
    description = build_mr_description(
        summary,
        task=task,
        trackers=args.trackers,
        cves=args.cves,
    )
    branch = args.source_branch or task
    url = open_package_mr(
        title=args.title,
        description=description,
        task=task,
        source_branch=branch,
        post_comment=post_comment,
        push=not args.no_push,
        trigger_review=not args.no_review,
    )
    print(f"MR={url}")
    return 0


def cmd_lookaside(args: argparse.Namespace) -> int:
    missing = [str(path) for path in args.files if not path.is_file()]
    if missing:
        raise RuntimeError(f"File not found: {', '.join(missing)}")
    print("Do not upload unless the user asks. Copy to /tmp then run:")
    for line in lookaside_upload_commands(args.files, args.package):
        print(line)
    return 0


def cmd_investigate(args: argparse.Namespace) -> int:
    if args.max_linked < 0:
        print("--max-linked must be >= 0", file=sys.stderr)
        return 2
    tickets, only_token = strip_only_keyword(list(args.tickets))
    find_related = args.find_related and not only_token and not args.only
    gathered = gather_ticket_reports(
        tickets, find_related=find_related, max_linked=args.max_linked
    )
    cve_ids: list[str] = []
    for report in gathered.reports:
        for cve_id in report.cve_ids:
            if cve_id not in cve_ids:
                cve_ids.append(cve_id)

    probes: dict[str, Any] = {
        "bot_mrs": {},
        "spec_deps": {},
        "sbom": {},
        "gitlab_mrs": [],
    }
    packages = []
    for report in gathered.reports:
        if report.ticket in gathered.user_provided and report.package_guess:
            if report.package_guess not in packages:
                packages.append(report.package_guess)

    for package in packages:
        try:
            probes["bot_mrs"][package] = {
                state: [asdict(entry) for entry in entries]
                for state, entries in list_bot_mrs(package).items()
            }
        except RuntimeError as err:
            probes["bot_mrs"][package] = {"error": str(err)}
        component = ""
        for report in gathered.reports:
            if report.package_guess == package and report.upstream_component:
                component = report.upstream_component
                break
        probes["spec_deps"][package] = [
            asdict(hit) for hit in probe_spec_deps(package, component)
        ]
        if args.sbom:
            nvr = ""
            ticket = ""
            for report in gathered.reports:
                if report.package_guess == package:
                    ticket = report.ticket
                    nvr = report.fixed_in_build
                    break
            try:
                meta = fetch_sbom(package, nvr=nvr, ticket=ticket)
                terms = [component] if component else []
                hits = search_sbom_file(Path(meta["path"]), terms) if terms else []
                probes["sbom"][package] = {
                    **meta,
                    "hits": [asdict(h) for h in hits],
                }
            except (OSError, RuntimeError, urllib.error.URLError) as err:
                probes["sbom"][package] = {"error": str(err)}

    if cve_ids:
        probes["gitlab_mrs"] = search_repo_fix_mrs(cve_ids)

    recap = recap_lines(gathered)
    recommendations = []
    for report in gathered.reports:
        hint, recommended_next = resolution_hint_for_report(
            report, has_task_or_mr=ticket_has_task_or_mr(report)
        )
        recommendations.append(
            {
                "ticket": report.ticket,
                "resolution_hint": hint,
                "recommended_next": recommended_next,
            }
        )
    payload = {
        "user_provided": gathered.user_provided,
        "discovered": gathered.discovered,
        "tickets": [asdict(r) for r in gathered.reports],
        "probes": probes,
        "recap": recap,
        "recommendations": recommendations,
        "suggested_chat_title": build_suggested_chat_title(gathered.reports),
    }
    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for report in gathered.reports:
        origin = (
            "user_provided" if report.ticket in gathered.user_provided else "discovered"
        )
        print(f"### {report.ticket} [{origin}]")
        print_ticket(report)
        print()
    if packages:
        print("### Probes")
        for package in packages:
            print(f"bot-mrs {package}:")
            mrs = probes["bot_mrs"].get(package) or {}
            if "error" in mrs:
                print(f"  bot-mrs error: {mrs['error']}")
            else:
                print(
                    f"  bot-mrs open={len(mrs.get('open', []))} "
                    f"merged={len(mrs.get('merged', []))}"
                )
            print(f"  spec-deps hits={len(probes['spec_deps'].get(package, []))}")
            if args.sbom:
                sbom = probes["sbom"].get(package) or {}
                if "error" in sbom:
                    print(f"  sbom error: {sbom['error']}")
                else:
                    print(f"  sbom hits={len(sbom.get('hits', []))}")
        print(f"  gitlab fix MRs: {len(probes['gitlab_mrs'])}")
        for hit in probes["gitlab_mrs"]:
            print(f"    {hit.get('state')}: {hit.get('html_url')}")
        print()
    print("\n".join(recap))
    print(f"\nSuggested chat title: {payload['suggested_chat_title']}")
    print("Wait for the user after this recap before Step 3 writes.")
    return 0


def cmd_version_check(args: argparse.Namespace) -> int:
    fib = args.fib
    affected = args.affected
    fixed = args.fixed
    analysis_nvr = args.analysis_nvr
    if args.ticket:
        gathered = gather_ticket_reports(
            [args.ticket], find_related=False, max_linked=0
        )
        if not gathered.reports:
            raise RuntimeError(f"Could not load {args.ticket}")
        report = gathered.reports[0]
        fib = fib or report.fixed_in_build
        affected = affected or report.affected_range
        fixed = fixed or report.fixed_version
        analysis_nvr = analysis_nvr or extract_analysis_nvr(report.cve_analysis_block)
    payload = version_check_payload(
        args.package,
        local_nvr=args.nvr,
        fib=fib,
        affected=affected,
        fixed=fixed,
        analysis_nvr=analysis_nvr,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"package: {payload['package']}")
    print(f"local_nvr: {payload['local_nvr'] or '(none)'}")
    print(f"local_version: {payload['local_version'] or '(none)'}")
    print(f"fib: {payload['fib'] or '(unset)'}")
    print(f"analysis_nvr: {payload['analysis_nvr'] or '(none)'}")
    print(f"affected: {payload['affected'] or '(none)'}")
    print(f"fixed: {payload['fixed'] or '(none)'}")
    print(f"vs_fixed: {payload['vs_fixed'] or '(n/a)'}")
    print(f"vs_affected: {payload['vs_affected'] or '(n/a)'}")
    print(f"hint: {payload['hint']}")
    print(payload["note"])
    return 0


def cmd_upstream_fix_age(args: argparse.Namespace) -> int:
    if not args.commit_date and not args.commit:
        raise RuntimeError("Need --commit-date or --commit")
    if not args.tag_date and not args.tag:
        raise RuntimeError("Need --tag-date or --tag")
    commit_dt, tag_dt, meta = resolve_fix_age_dates(
        commit=args.commit,
        tag=args.tag,
        repo=args.repo,
        commit_date=args.commit_date,
        tag_date=args.tag_date,
    )
    age = compare_fix_age(commit_dt, tag_dt)
    payload = {
        **meta,
        "commit_date": commit_dt.isoformat(),
        "tag_date": tag_dt.isoformat(),
        **age,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"commit: {payload.get('commit') or args.commit or '(flag)'}")
    print(f"tag: {payload.get('tag') or args.tag or '(flag)'}")
    print(f"commit_date: {payload['commit_date']} ({payload['commit_date_source']})")
    print(f"tag_date: {payload['tag_date']} ({payload['tag_date_source']})")
    print(f"verdict: {payload['verdict']}")
    print(f"hint: {payload['hint']}")
    return 0


def cmd_release_bump(args: argparse.Namespace) -> int:
    if getattr(args, "apply", False):
        payload = apply_release_bump(args.package)
    else:
        payload = release_bump_payload(args.package)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"package: {payload['package']}")
    print(f"modification_status: {payload['modification_status'] or '(unset)'}")
    print(f"metadata_release: {payload['metadata_release'] or '(unset)'}")
    print(f"spec_release: {payload['spec_release'] or '(none)'}")
    print(f"proposed_spec_release: {payload['proposed_spec_release'] or '(n/a)'}")
    print(f"writes: {'yes' if payload.get('writes') else 'no'}")
    print(f"hint: {payload['hint']}")
    print(payload["note"])
    return 0


def cmd_log_message(args: argparse.Namespace) -> int:
    file_path = args.file
    agent_values = {
        "opencode": args.opencode,
        "claude": args.claude,
        "cursor": args.cursor,
    }
    selected = {name: val for name, val in agent_values.items() if val is not None}
    if selected:
        if file_path is not None:
            names = "/".join(f"--{n}" for n in selected)
            print(f"error: pass either FILE or {names}, not both", file=sys.stderr)
            return 2
        (agent, session_id), = selected.items()
        file_path = export_agent_log(agent, session_id or None)
        print(f"Exported {agent} session -> {file_path}")
    elif file_path is None:
        print(
            "error: FILE or one of --opencode/--claude/--cursor is required",
            file=sys.stderr,
        )
        return 2

    target_file, msg = log_message(
        ticket=args.ticket,
        file_path=file_path,
        print_only=args.print_only,
    )
    print(f"Log: {target_file}")
    if not args.print_only:
        print(msg)
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
        "deps": cmd_deps,
        "comment": cmd_comment,
        "next-release": cmd_next_release,
        "set-fib": cmd_set_fib,
        "close-nab": cmd_close_nab,
        "create-task": cmd_create_task,
        "open-mr": cmd_open_mr,
        "lookaside-cmd": cmd_lookaside,
        "investigate": cmd_investigate,
        "version-check": cmd_version_check,
        "upstream-fix-age": cmd_upstream_fix_age,
        "release-bump": cmd_release_bump,
        "log-message": cmd_log_message,
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
