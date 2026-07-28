#!/usr/bin/env python3
"""Helper utilities for HUM CVE ticket triage.

This script centralizes repetitive /cve skill ticket-gathering work so the
agent can consume a compact, structured summary instead of repeatedly
executing and parsing long command outputs in-chat.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,8}")
HUM_RE = re.compile(r"HUM-\d{3,6}")
MR_URL_RE = re.compile(
    r"https://gitlab\.com/redhat/hummingbird/rpms/-/merge_requests/\d+"
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


def run_command(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True)
    except OSError as err:
        raise RuntimeError(f"Failed to execute {' '.join(args)}: {err}") from err


def uses_vendored_deps(package: str) -> bool:
    # 4 parents up: .cursor/skills/cve → .cursor/skills → .cursor → repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    go_vendor_path = repo_root / "rpms" / package / "go-vendor-tools.toml"
    return go_vendor_path.exists()


def normalize_hum_key(value: str) -> str:
    trimmed = value.strip().upper()
    if trimmed.startswith("HUM-"):
        return trimmed
    if re.fullmatch(r"\d+", trimmed):
        return f"HUM-{trimmed}"
    raise ValueError(f"Invalid ticket key: {value}")


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
    result = run_command(
        ["rhjira", "list", jql, "--fields", "key", "--rawoutput", "--noheader"]
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
    result = run_command(["rhjira", "dump", key, "--fields", fields, "--json"])
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


### CLI ###


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize HUM CVE ticket details for /cve workflow.",
    )
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as err:
        print(f"Input error: {err}", file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as err:
        print(f"Runtime error: {err}", file=sys.stderr)
        raise SystemExit(1)
