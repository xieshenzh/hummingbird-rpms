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

### Dataclasses ###


@dataclass
class LinkedTicket:
    ticket: str
    summary: str = ""
    status: str = ""
    ticket_type: str = ""
    error: str = ""


@dataclass
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
    linked_ticket_details: list[LinkedTicket] | None = None
    embargoed: bool = False
    assessment: str = ""
    affected_range: str = ""
    fixed_version: str = ""
    cve_analysis_block: str = ""


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


### Utilities ###


def run_command(args: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as err:
        raise RuntimeError(f"Failed to execute {' '.join(args)}: {err}") from err
    return CommandResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def normalize_hum_key(value: str) -> str:
    trimmed = value.strip().upper()
    if trimmed.startswith("HUM-"):
        return trimmed
    if re.fullmatch(r"\d+", trimmed):
        return f"HUM-{trimmed}"
    raise ValueError(f"Invalid ticket key: {value}")


_raw_cache: dict[str, str] = {}


def fetch_raw(key: str) -> str:
    if key in _raw_cache:
        return _raw_cache[key]
    result = run_command(["rhjira", "show", key])
    if result.returncode != 0:
        raise RuntimeError(
            f"rhjira show {key} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    _raw_cache[key] = result.stdout
    return result.stdout


### Extraction ###


def extract_field(text: str, field_name: str) -> str:
    match = re.search(
        # Keep matching on the same line only. Using \s* here allows newline
        # consumption when a field is empty and can leak into the next section.
        rf"^{re.escape(field_name)}:[ \t]*(.*)$",
        text,
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def extract_summary(text: str, ticket_key: str) -> str:
    summary = extract_field(text, "Summary")
    if summary:
        return summary

    # Newer rhjira show output places summary in the banner header:
    #   HUM-XXXX: <summary text>
    match = re.search(
        rf"^{re.escape(ticket_key)}:\s*(.+)$",
        text,
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def extract_field_block(text: str, field_name: str) -> str:
    """Extract a Jira field value that may span multiple lines."""
    match = re.search(
        rf"^{re.escape(field_name)}:\s*(.*?)(?=^\S[^:\n]*:\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


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


def extract_assessment(block: str) -> str:
    m = re.search(r"^ASSESSMENT:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_vendor_field(block: str, field: str) -> str:
    m = re.search(rf"^\s{{2}}{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


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


def parse_linked_keys(text: str) -> list[str]:
    linked_fields = [
        "Is Blocked By",
        "Blocks",
        "Issue Links",
    ]
    linked_text = "\n".join(
        extract_field_block(text, field_name) for field_name in linked_fields
    )
    keys = HUM_RE.findall(linked_text)
    return list(dict.fromkeys(keys))


### Parsing ###


def parse_ticket_blob(ticket_key: str, text: str) -> TicketReport:
    summary = extract_summary(text, ticket_key)
    labels = extract_field(text, "Labels")
    fixed_in_build = extract_field(text, "Fixed in Build")
    status = extract_field(text, "Status")
    severity = extract_field(text, "Severity")
    assignee = extract_field(text, "Assignee")
    ticket_type = extract_field(text, "Ticket Type")
    cve_ids = sorted(set(CVE_RE.findall(text)))
    linked_keys = parse_linked_keys(text)
    cve_analysis = extract_cve_analysis_block(text)
    assessment = extract_assessment(cve_analysis) if cve_analysis else ""
    affected_range = (
        extract_vendor_field(cve_analysis, "Affected") if cve_analysis else ""
    )
    fixed_version = (
        extract_vendor_field(cve_analysis, "Fixed in") if cve_analysis else ""
    )
    mr_links = sorted(set(MR_URL_RE.findall(text)))
    package_guess = extract_package_guess(summary, labels, fixed_in_build)
    embargoed = summary.upper().startswith("EMBARGOED")

    return TicketReport(
        ticket=ticket_key,
        summary=summary,
        status=status,
        severity=severity,
        assignee=assignee,
        ticket_type=ticket_type,
        labels=labels,
        fixed_in_build=fixed_in_build,
        cve_ids=cve_ids,
        package_guess=package_guess,
        linked_keys=linked_keys,
        mr_links_in_ticket=mr_links,
        embargoed=embargoed,
        assessment=assessment,
        affected_range=affected_range,
        fixed_version=fixed_version,
        cve_analysis_block=cve_analysis,
    )


def _parse_list_output(stdout: str, field_count: int) -> list[list[str]]:
    """Parse pipe-delimited rhjira list --rawoutput output.

    *field_count* is the number of --fields requested (excluding the leading
    line-number column that --rawoutput always prepends).
    Returns a list of rows, each row being the field values as a list.
    """
    rows: list[list[str]] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 1 + field_count:
            continue
        rows.append([parts[i + 1].strip() for i in range(field_count)])
    return rows


def batch_fetch_linked(keys: list[str]) -> dict[str, LinkedTicket]:
    """Fetch multiple linked tickets in a single rhjira list call."""
    if not keys:
        return {}

    key_list = ", ".join(keys)
    jql = f"key in ({key_list})"
    result = run_command([
        "rhjira", "list", jql,
        "--fields", "key,summary,status,issuetype",
        "--rawoutput", "--noheader", "--summarylength", "0",
    ])

    if result.returncode != 0:
        return {}

    fetched: dict[str, LinkedTicket] = {}
    for row in _parse_list_output(result.stdout, 4):
        fetched[row[0]] = LinkedTicket(
            ticket=row[0],
            summary=row[1],
            status=row[2],
            ticket_type=row[3],
        )
    return fetched


def batch_fetch_tickets(keys: list[str]) -> dict[str, TicketReport]:
    """Fetch multiple tickets in a single rhjira list call.

    Returns TicketReport objects with structured fields populated.
    Custom fields (severity, fixed_in_build) and issuelinks are not
    available via rhjira list, so those fields are left empty.
    """
    if not keys:
        return {}

    key_list = ", ".join(keys)
    jql = f"key in ({key_list})"
    result = run_command([
        "rhjira", "list", jql,
        "--fields", "key,summary,status,issuetype,assignee,labels",
        "--rawoutput", "--noheader", "--summarylength", "0",
    ])

    if result.returncode != 0:
        return {}

    reports: dict[str, TicketReport] = {}
    for row in _parse_list_output(result.stdout, 6):
        ticket, summary, status, ticket_type, assignee, labels = row
        cve_ids = sorted(set(CVE_RE.findall(summary)))
        package_guess = extract_package_guess(summary, labels, "")
        embargoed = summary.upper().startswith("EMBARGOED")

        reports[ticket] = TicketReport(
            ticket=ticket,
            summary=summary,
            status=status,
            severity="",
            assignee=assignee,
            ticket_type=ticket_type,
            labels=labels,
            fixed_in_build="",
            cve_ids=cve_ids,
            package_guess=package_guess,
            linked_keys=[],
            mr_links_in_ticket=[],
            embargoed=embargoed,
        )
    return reports


def inspect_linked_tickets(
    current_ticket: str,
    linked_keys: list[str],
    max_linked: int,
) -> list[LinkedTicket]:
    keys_to_fetch = [
        k for k in linked_keys if k != current_ticket
    ][:max_linked]

    if not keys_to_fetch:
        return []

    fetched = batch_fetch_linked(keys_to_fetch)

    linked_info: list[LinkedTicket] = []
    for key in keys_to_fetch:
        if key in fetched:
            linked_info.append(fetched[key])
        else:
            linked_info.append(LinkedTicket(ticket=key, error="not found in batch fetch"))

    return linked_info


### Output ###


def print_ticket(report: TicketReport) -> None:
    if report.embargoed:
        print(f"*** EMBARGOED: {report.ticket} — do not analyze or act. ***")
        return
    pkg = report.package_guess or "?"
    sev = report.severity or "-"
    fib = "set" if report.fixed_in_build else "-"
    mr_ids = []
    for u in report.mr_links_in_ticket:
        m = re.search(r"/merge_requests/(\d+)", u)
        if m:
            mr_ids.append(m.group(1))
    mr = ",".join(mr_ids) if mr_ids else "-"
    cvs = ",".join(report.cve_ids) if report.cve_ids else "-"
    assessment = report.assessment or "-"
    print(
        f"{report.ticket} | {pkg} | {report.status} | {sev} | {report.assignee} | FIB:{fib} | MR:{mr} | {cvs} | {assessment}"
    )
    for u in report.mr_links_in_ticket:
        print(f"  MR: {u}")
    if report.affected_range:
        print(f"  Affected: {report.affected_range}")
    if report.fixed_version:
        print(f"  Fixed in: {report.fixed_version}")
    for linked in report.linked_ticket_details or []:
        if linked.error:
            print(f"  LINK: {linked.ticket} ERROR {linked.error}")
            continue
        print(
            f"  LINK: {linked.ticket} [{linked.ticket_type}] {linked.status} - {linked.summary}"
        )


def build_suggested_chat_title(reports: list[TicketReport]) -> str:
    if not reports:
        return ""

    tickets = [r.ticket for r in reports]
    packages = [r.package_guess for r in reports]

    if len(reports) == 1:
        return f"{tickets[0]} {packages[0]}" if packages[0] else tickets[0]

    all_have_package = all(packages)
    if all_have_package and len(set(packages)) == 1:
        return f"{' '.join(tickets)} {packages[0]}"

    parts: list[str] = []
    for ticket, package in zip(tickets, packages):
        parts.append(f"{ticket} {package}" if package else ticket)
    return ", ".join(parts)


def apply_title_prefix(title: str, prefix: str) -> str:
    cleaned_prefix = prefix.strip()
    cleaned_title = title.strip()
    if not cleaned_prefix:
        return cleaned_title
    if not cleaned_title:
        return cleaned_prefix
    return f"{cleaned_prefix} {cleaned_title}"


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


def ensure_rhjira_available() -> bool:
    """Check whether rhjira is available in PATH."""
    if shutil.which("rhjira"):
        return True

    print("WARNING: `rhjira` is not installed or not found in PATH.", file=sys.stderr)
    print("Install it with:", file=sys.stderr)
    print("  pip install rhjira", file=sys.stderr)
    print("Then configure authentication with:", file=sys.stderr)
    print("  rhjira settoken", file=sys.stderr)
    return False


def find_related_tickets(initial_cve_ids: list[str]) -> list[str]:
    """Search for all open HUM tickets that reference the given CVE IDs.

    Returns a list of unique ticket keys (e.g., HUM-3120).
    """
    if not initial_cve_ids:
        return []

    all_tickets: set[str] = set()

    for cve_id in initial_cve_ids:
        jql = f'project = HUM AND status not in (Closed, "CLOSED (invalid)", Done, Resolved, "Won\'t Fix") AND text ~ "{cve_id}"'
        result = run_command(
            ["rhjira", "list", jql, "--fields", "key", "--rawoutput", "--noheader"]
        )

        if result.returncode != 0:
            print(
                f"Warning: Failed to search for tickets with {cve_id}: {result.stderr}",
                file=sys.stderr,
            )
            continue

        for line in result.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 2:
                candidate = parts[1].strip()
                if HUM_RE.fullmatch(candidate):
                    all_tickets.add(candidate)

    return sorted(all_tickets)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.max_linked < 0:
        print("--max-linked must be >= 0", file=sys.stderr)
        return 2

    if not ensure_rhjira_available():
        return 1

    initial_keys: list[str] = [normalize_hum_key(key) for key in args.tickets]
    ticket_keys: list[str] = list(initial_keys)
    initial_set = set(initial_keys)

    if args.find_related:
        print("Finding related tickets...", file=sys.stderr)
        initial_cve_ids: set[str] = set()
        for key in initial_keys:
            try:
                text = fetch_raw(key)
            except RuntimeError:
                print(f"Warning: Failed to fetch {key}, skipping", file=sys.stderr)
                continue
            cve_ids = CVE_RE.findall(text)
            initial_cve_ids.update(cve_ids)

        if initial_cve_ids:
            related_keys = find_related_tickets(sorted(initial_cve_ids))
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

    # Full fetch for user-provided tickets (need custom fields, comments)
    initial_reports: dict[str, TicketReport] = {}
    for key in initial_keys:
        try:
            text = fetch_raw(key)
        except RuntimeError as e:
            print(f"Warning: failed to fetch {key}: {e}", file=sys.stderr)
            continue
        print(f"Processing {key}...", file=sys.stderr, end="\r")
        initial_reports[key] = parse_ticket_blob(key, text)

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
    all_reports: dict[str, TicketReport] = {}
    for key in ticket_keys:
        if key in initial_reports:
            all_reports[key] = initial_reports[key]
        elif key in discovered_reports:
            all_reports[key] = discovered_reports[key]

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
