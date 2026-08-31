import os
import sys
import urllib.error
from pathlib import Path
from typing import Any, Literal

from lib.models import (
    TicketReport,
    GatheredTickets,
    GITLAB_RPMS_REPO,
)
from lib.utils import normalize_hum_key
from lib.jira import (
    ensure_rhjira_available,
    fetch_json,
    parse_ticket_json,
    find_related_tickets,
    batch_fetch_tickets,
    inspect_linked_tickets,
    run_rhjira,
)

from cve_analysis_bridge import AnalysisImportError, gitlab_client_module

# ---- Ticket gathering ----


def strip_only_keyword(tickets: list[str]) -> tuple[list[str], bool]:
    """Honor a trailing `only` token: named tickets, no find-related."""
    if tickets and tickets[-1].lower() == "only":
        named = tickets[:-1]
        if not named:
            raise ValueError("`only` must follow at least one ticket key")
        return named, True
    return tickets, False


def gather_ticket_reports(
    tickets: list[str],
    *,
    find_related: bool,
    max_linked: int,
) -> GatheredTickets:
    ensure_rhjira_available()
    initial_keys = [normalize_hum_key(key) for key in tickets]
    initial_set = set(initial_keys)
    ticket_keys = list(initial_keys)
    initial_reports: dict[str, TicketReport] = {}
    for key in initial_keys:
        try:
            fields = fetch_json(key)
        except RuntimeError as err:
            print(f"Warning: failed to fetch {key}: {err}", file=sys.stderr)
            continue
        print(f"Processing {key}...", file=sys.stderr, end="\r")
        initial_reports[key] = parse_ticket_json(key, fields)  # type: ignore[arg-type]

    if find_related:
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

    discovered_keys = [k for k in ticket_keys if k not in initial_set]
    discovered_reports: dict[str, TicketReport] = {}
    if discovered_keys:
        print(
            f"Batch-fetching {len(discovered_keys)} discovered tickets...",
            file=sys.stderr,
        )
        discovered_reports = batch_fetch_tickets(discovered_keys)

    reports: list[TicketReport] = []
    for key, parsed in {**initial_reports, **discovered_reports}.items():
        parsed.linked_ticket_details = inspect_linked_tickets(
            current_ticket=key,
            linked_keys=parsed.linked_keys,
            max_linked=max_linked,
        )
        reports.append(parsed)
    return GatheredTickets(
        reports=reports,
        user_provided=[k for k in initial_keys if k in initial_reports],
        discovered=discovered_keys,
    )


# ---- GitLab MR search ----


def search_repo_fix_mrs(cve_ids: list[str]) -> list[dict[str, Any]]:
    try:
        gl_mod = gitlab_client_module()
    except AnalysisImportError as err:
        print(f"Warning: skipping GitLab MR search: {err}", file=sys.stderr)
        return []
    token = os.environ.get("GITLAB_TOKEN")
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cve_id in cve_ids:
        try:
            found = gl_mod.search_gitlab_mrs(
                "gitlab.com", GITLAB_RPMS_REPO, cve_id, token
            )
        except (
            OSError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            TypeError,
        ) as err:
            print(
                f"Warning: GitLab MR search failed for {cve_id}: {err}", file=sys.stderr
            )
            continue
        for hit in found or []:
            url = str(hit.get("html_url") or "")
            if url and url not in seen:
                seen.add(url)
                hits.append(hit)
    return hits


# ---- Resolution hints ----

ResolutionHint = Literal[
    "embargoed_stop",
    "fib_leave_for_advisory",
    "fib_ask_create_task",
    "needs_version_check",
    "needs_package_guess",
]


def ticket_has_task_or_mr(report: TicketReport) -> bool:
    """True if a linked ticket or an in-ticket MR link already exists."""
    return bool(report.linked_ticket_details) or bool(report.mr_links_in_ticket)


def resolution_hint_for_report(
    report: TicketReport, *, has_task_or_mr: bool
) -> tuple[ResolutionHint, str]:
    """Return (resolution_hint, recommended_next) for one ticket's recap line.

    This is a suggestion, not a decision. It does not replace SKILL.md Step 3:
    the agent still waits for the user, still gathers evidence before closing,
    and never claims Done-Errata from versions alone.
    """
    if report.embargoed:
        return "embargoed_stop", "Stop; embargoed ticket. Do not analyze or write."
    if report.fixed_in_build and has_task_or_mr:
        return (
            "fib_leave_for_advisory",
            (
                "FIB is set and a task/MR exists; leave for advisory automation "
                "unless the user asks."
            ),
        )
    if report.fixed_in_build:
        return (
            "fib_ask_create_task",
            "FIB is set but no task/MR is linked; ask whether to create the task.",
        )
    if report.package_guess:
        return (
            "needs_version_check",
            (
                f"version-check {report.package_guess} --ticket {report.ticket} "
                "to compare the local NVR against the CVE range before deciding."
            ),
        )
    return (
        "needs_package_guess",
        (
            "No package guess; run spec-deps/sbom to identify the affected package "
            "before version-check."
        ),
    )


# ---- Recap generation ----


def recap_lines(gathered: GatheredTickets) -> list[str]:
    user_set = set(gathered.user_provided)
    lines: list[str] = ["Recap:"]
    for report in gathered.reports:
        origin = "user_provided" if report.ticket in user_set else "discovered"
        lines.append(
            f"{report.ticket} [{origin}] {report.ticket_type} {report.status} | "
            f"assignee={report.assignee or '(unassigned)'} | "
            f"labels={report.labels or '(none)'} | "
            f"fib={report.fixed_in_build or '(unset)'} | "
            f"pkg={report.package_guess or '?'}"
        )
        hint, recommended_next = resolution_hint_for_report(
            report, has_task_or_mr=ticket_has_task_or_mr(report)
        )
        lines.append(f"  resolution_hint={hint}")
        lines.append(f"  recommended_next: {recommended_next}")
    if gathered.discovered:
        related = []
        by_ticket = {r.ticket: r for r in gathered.reports}
        for key in gathered.discovered:
            pkg = getattr(by_ticket.get(key), "package_guess", "") or "?"
            related.append(f"{key} ({pkg})")
        lines.append(
            "Related: "
            + ", ".join(related)
            + ". Apply this to all, or only the tickets you named?"
        )
        lines.append(
            'A "Yes please" on the named set is not approval for discovered siblings.'
        )
    return lines


# ---- Log / Transcript Attachment ----


def attach_file(ticket: str, file_path: Path) -> str:
    """Attach a log or transcript file to a Jira ticket."""
    ticket = normalize_hum_key(ticket)
    if not file_path.is_file():
        raise ValueError(f"File not found: {file_path}")

    result = run_rhjira(["attach", ticket, str(file_path)])
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to attach {file_path} to {ticket}: "
            f"{result.stderr or result.stdout or 'unknown error'}"
        )
    return f"Attached {file_path.name} to {ticket}"


def log_message(
    ticket: str,
    file_path: Path,
    *,
    print_only: bool = False,
) -> tuple[Path, str]:
    """Attach an existing log or transcript file to a Jira ticket."""
    ticket = normalize_hum_key(ticket)
    if not file_path.is_file():
        raise ValueError(f"Log file not found: {file_path}")

    if print_only:
        return file_path, f"Found {file_path} (print-only, not uploaded)"

    msg = attach_file(ticket, file_path)
    return file_path, msg
