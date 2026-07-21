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
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CVE_RE = re.compile(r"CVE-\d{4}-\d{4,8}")
HUM_RE = re.compile(r"HUM-\d{3,6}")
MR_URL_RE = re.compile(
    r"https://gitlab\.com/redhat/hummingbird/rpms/-/merge_requests/\d+"
)


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def run_command(args: list[str]) -> CommandResult:
    """Run a command and return captured output."""
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
    # Accept only bare positive integer ticket numbers (e.g. "3120").
    if re.fullmatch(r"\d+", trimmed):
        return f"HUM-{trimmed}"
    raise ValueError(f"Invalid ticket key: {value}")


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
    noformat_blocks = re.findall(r"\{noformat\}(.*?)\{noformat\}", text, flags=re.DOTALL)
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


def parse_linked_keys(text: str) -> list[str]:
    linked_fields = [
        "Is Blocked By",
        "Blocks",
        "Issue Links",
    ]
    linked_text = "\n".join(extract_field_block(text, field_name) for field_name in linked_fields)
    keys = HUM_RE.findall(linked_text)
    # Preserve order while deduplicating.
    ordered: list[str] = []
    for key in keys:
        if key not in ordered:
            ordered.append(key)
    return ordered


def parse_ticket_blob(ticket_key: str, text: str) -> dict[str, Any]:
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
    mr_links = sorted(set(MR_URL_RE.findall(text)))
    package_guess = extract_package_guess(summary, labels, fixed_in_build)
    embargoed = summary.upper().startswith("EMBARGOED")

    return {
        "ticket": ticket_key,
        "summary": summary,
        "status": status,
        "severity": severity,
        "assignee": assignee,
        "ticket_type": ticket_type,
        "labels": labels,
        "fixed_in_build": fixed_in_build,
        "cve_ids": cve_ids,
        "package_guess": package_guess,
        "linked_keys": linked_keys,
        "mr_links_in_ticket": mr_links,
        "embargoed": embargoed,
        "cve_analysis_block": cve_analysis,
    }


def inspect_linked_tickets(
    current_ticket: str,
    linked_keys: list[str],
    max_linked: int,
) -> list[dict[str, Any]]:
    linked_info: list[dict[str, Any]] = []
    inspected = 0

    for key in linked_keys:
        if key == current_ticket:
            continue
        if inspected >= max_linked:
            break

        result = run_command(["rhjira", "show", key])
        inspected += 1
        if result.returncode != 0:
            linked_info.append(
                {
                    "ticket": key,
                    "error": result.stderr.strip() or result.stdout.strip() or "rhjira show failed",
                }
            )
            continue

        blob = parse_ticket_blob(key, result.stdout)
        linked_info.append(
            {
                "ticket": key,
                "summary": blob["summary"],
                "status": blob["status"],
                "ticket_type": blob["ticket_type"],
                "mr_links": blob["mr_links_in_ticket"],
            }
        )

    return linked_info


def triage_ticket(ticket_key: str, max_linked: int) -> dict[str, Any]:
    result = run_command(["rhjira", "show", ticket_key])
    if result.returncode != 0:
        raise RuntimeError(
            f"rhjira show {ticket_key} failed: {result.stderr.strip() or result.stdout.strip()}"
        )

    parsed = parse_ticket_blob(ticket_key, result.stdout)
    linked_details = inspect_linked_tickets(
        current_ticket=ticket_key,
        linked_keys=parsed["linked_keys"],
        max_linked=max_linked,
    )
    parsed["linked_ticket_details"] = linked_details
    parsed["raw_output_available"] = True
    return parsed


def print_human_report(report: dict[str, Any]) -> None:
    print(f"Ticket: {report['ticket']}")
    print(f"Summary: {report['summary']}")
    print(f"Status: {report['status']} | Severity: {report['severity']} | Assignee: {report['assignee']}")
    print(f"Package guess: {report['package_guess'] or '(unknown)'}")
    print(f"CVE IDs: {', '.join(report['cve_ids']) if report['cve_ids'] else '(none found)'}")
    print(f"Fixed in Build: {report['fixed_in_build'] or '(not set)'}")
    print(f"Embargoed: {'YES' if report['embargoed'] else 'no'}")

    if report["mr_links_in_ticket"]:
        print("MR links on ticket:")
        for url in report["mr_links_in_ticket"]:
            print(f"  - {url}")

    if report["linked_keys"]:
        print(f"Linked tickets: {', '.join(report['linked_keys'])}")
    else:
        print("Linked tickets: (none)")

    if report["linked_ticket_details"]:
        print("Linked ticket details:")
        for linked in report["linked_ticket_details"]:
            if "error" in linked:
                print(f"  - {linked['ticket']}: ERROR {linked['error']}")
                continue
            mr_part = f" | MR: {', '.join(linked['mr_links'])}" if linked["mr_links"] else ""
            print(
                "  - "
                f"{linked['ticket']} [{linked['ticket_type']}] {linked['status']} - {linked['summary']}{mr_part}"
            )

    if report["cve_analysis_block"]:
        print("\n---- cve_analysis {noformat} excerpt ----")
        print(report["cve_analysis_block"])
        print("---- end excerpt ----")
    else:
        print("\nNo cve_analysis {noformat} block found.")


def build_suggested_chat_title(reports: list[dict[str, Any]]) -> str:
    """Build a deterministic chat title from helper output.

    Format matches the skill convention:
    - single ticket: HUM-XXXX <package>
    - same package, multiple tickets: HUM-XXXX HUM-YYYY <package>
    - mixed packages: HUM-XXXX <package>, HUM-YYYY <package2>
    """
    if not reports:
        return ""

    tickets = [str(report.get("ticket", "")).strip() for report in reports]
    packages = [str(report.get("package_guess", "")).strip() for report in reports]

    if len(reports) == 1:
        package = packages[0]
        return f"{tickets[0]} {package}" if package else tickets[0]

    all_have_package = all(packages)
    if all_have_package and len(set(packages)) == 1:
        return f"{' '.join(tickets)} {packages[0]}"

    parts: list[str] = []
    for ticket, package in zip(tickets, packages):
        parts.append(f"{ticket} {package}" if package else ticket)
    return ", ".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize HUM CVE ticket details for /cve workflow.",
    )
    parser.add_argument(
        "tickets",
        nargs="+",
        help="HUM ticket key(s), e.g. HUM-3120 or 3120",
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.max_linked < 0:
        print("--max-linked must be >= 0", file=sys.stderr)
        return 2

    if not ensure_rhjira_available():
        return 1

    reports: list[dict[str, Any]] = []
    for key in args.tickets:
        normalized = normalize_hum_key(key)
        report = triage_ticket(normalized, max_linked=args.max_linked)
        reports.append(report)

    suggested_chat_title = build_suggested_chat_title(reports)
    payload: dict[str, Any] = {
        "suggested_chat_title": suggested_chat_title,
        "tickets": reports,
    }

    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    if args.title_only:
        print(suggested_chat_title)
    elif args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for idx, report in enumerate(reports):
            if idx:
                print("\n" + "=" * 72 + "\n")
            print_human_report(report)
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
