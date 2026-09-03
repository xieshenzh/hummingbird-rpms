import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

from lib.models import (
    LinkedTicket,
    TicketReport,
    CVE_RE,
    HUM_RE,
    MR_URL_RE,
    extract_package_guess,
)
from lib.utils import (
    run_command,
    normalize_hum_key,
    normalize_nvr,
    json_str,
    extract_vendor_field,
)

# Bridge imports (lib/utils.py has already ensured the skill dir is on sys.path)
from cve_analysis_bridge import (  # noqa: E402
    jira_client_module,
    load_jira_auth,
    pulp_module,
)


# ---- Constants ----

TRANSIENT_RHJIRA_RE = re.compile(
    r"proxy|tunnel|timed out|timeout|temporar|502|503|504|connection reset|\beof\b",
    re.IGNORECASE,
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
CREATED_TICKET_RE = re.compile(
    r"https://redhat\.atlassian\.net/browse/(HUM-\d{3,6})|(?:^|\b)(HUM-\d{3,6})\b"
)
_json_cache: dict[str, dict[str, Any]] = {}
_auth_cache = None  # JiraAuth, cached after first call


def clear_jira_auth_cache() -> None:
    """Reset Jira auth cache (used in tests to ensure test isolation)."""
    global _auth_cache
    _auth_cache = None


# ---- rhjira subprocess ----


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


def ensure_rhjira_available() -> None:
    if shutil.which("rhjira"):
        return
    raise RuntimeError(
        "`rhjira` is not installed or not found in PATH.\n"
        "Install it with: pip install rhjira\n"
        "Then configure authentication with: rhjira settoken"
    )


# ---- Extraction (ticket field parsing) ----


def _extract_assignee(fields: dict[str, Any]) -> str:
    assignee = json_str(fields, "assignee", "displayName")
    if not assignee:
        assignee = json_str(fields, "assignee", "emailAddress")
    return assignee


def _extract_labels(fields: dict[str, Any]) -> str:
    raw = fields.get("labels")
    return ",".join(raw) if isinstance(raw, list) else ""


def extract_assessment(block: str) -> str:
    m = re.search(r"^ASSESSMENT:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_flaw_summary(description: str) -> str:
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


def _comment_bodies(fields: dict[str, Any]) -> list[str]:
    comment_data = fields.get("comment")
    if not comment_data or not isinstance(comment_data, dict):
        return []
    return [
        c["body"]
        for c in comment_data.get("comments", [])
        if isinstance(c, dict) and c.get("body")
    ]


# ---- Ticket parsing ----


def parse_ticket_json(ticket_key: str, fields: dict[str, Any]) -> TicketReport:
    # Extract standard Jira fields (summary, status, assignee, severity, FIB)
    summary = fields.get("summary", "")
    status = json_str(fields, "status", "name")
    ticket_type = json_str(fields, "issuetype", "name")
    assignee = _extract_assignee(fields)
    severity = json_str(fields, "customfield_10840", "value")
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


# ---- Fetch ----


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
            status=json_str(fields, "status", "name"),
            ticket_type=json_str(fields, "issuetype", "name"),
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
        labels = _extract_labels(fields)
        result[key] = TicketReport(
            ticket=key,
            summary=summary,
            status=json_str(fields, "status", "name"),
            ticket_type=json_str(fields, "issuetype", "name"),
            assignee=_extract_assignee(fields),
            labels=labels,
            severity="",
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


def find_related_tickets(initial_cve_ids: list[str]) -> list[str]:
    if not initial_cve_ids:
        return []

    or_clauses = " OR ".join(f'text ~ "{c}"' for c in initial_cve_ids)
    jql = f'project = HUM AND status not in (Closed, "CLOSED (invalid)", Done, Resolved, "Won\'t Fix") AND ({or_clauses})'
    result = run_rhjira(["list", jql, "--fields", "key", "--rawoutput", "--noheader"])
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


# ---- Pulp ----


def pulp_has_nvr(package: str, nvr: str) -> tuple[bool, str]:
    """Return whether the SRPM NVR is listed in Hummingbird Pulp."""
    wanted = normalize_nvr(nvr)
    wanted_file = f"{wanted}.src.rpm"
    pulp = pulp_module()
    index = pulp.fetch_srpm_listing_index(package)
    if index is not None:
        nvrs = list(getattr(index, "nvrs", []) or [])
        files = set(getattr(index, "srpm_filenames", []) or [])
        if wanted in nvrs or wanted_file in files:
            return True, f"{wanted_file} is in the Pulp listing for {package}"
    latest = pulp.fetch_hummingbird_latest_srpm(package)
    if latest and (latest == wanted_file or wanted in str(latest)):
        return True, f"Pulp latest SRPM for {package} is {latest}"
    latest_note = f" (latest {latest})" if latest else ""
    return False, f"{wanted_file} not published in Pulp for {package}{latest_note}"


# ---- Jira writes ----


def _jira_auth_call(method: str, ticket: str, *args: Any, **kwargs: Any) -> Any:
    global _auth_cache
    jc = jira_client_module()
    if _auth_cache is None:
        _auth_cache = load_jira_auth()
    auth = _auth_cache
    fn = getattr(jc, method)
    try:
        return fn(
            auth.base_url,
            auth.token,
            ticket,
            *args,
            basic_auth_user=auth.basic_auth_user,
            **kwargs,
        )
    except (
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
    ) as err:
        raise RuntimeError(f"Jira {method} failed for {ticket}: {err}") from err


def post_comment(ticket: str, body: str) -> bool:
    return bool(_jira_auth_call("jira_add_comment", ticket, body))


def apply_next_release(ticket: str, body: str) -> None:
    post_comment(ticket, body)
    _jira_auth_call("add_jira_label", ticket, "cve-next-release")
    _jira_auth_call("remove_jira_label", ticket, "cve-needs-attention")


def set_fixed_in_build(
    ticket: str,
    nvr: str,
    package: str,
    *,
    force: bool = False,
) -> str:
    published, detail = pulp_has_nvr(package, nvr)
    if not published and not force:
        raise RuntimeError(
            f"Refusing to set Fixed in Build: {detail}. "
            "Pass --force only if the user confirmed a Pulp override."
        )
    fib = f"{normalize_nvr(nvr)}.src.rpm"
    _jira_auth_call("set_fixed_in_build", ticket, fib)
    return detail if published else f"forced; {detail}"


def close_not_a_bug(ticket: str, body: str, vex: str) -> None:
    """Comment, set VEX, and close as Not a Bug.

    jira_client raises on HTTP errors. set_vex_justification and
    jira_resolve_issue return False when the field/transition is missing.
    None (or any other non-False value) is treated as success so a 204-style
    empty body cannot abort after the comment was posted.
    """
    post_comment(ticket, body)
    vex_ok = _jira_auth_call("set_vex_justification", ticket, vex)
    if vex_ok is False:
        raise RuntimeError(f"Failed to set VEX justification on {ticket}")
    resolved = _jira_auth_call(
        "jira_resolve_issue",
        ticket,
        "Closed",
        "Not a Bug",
    )
    if resolved is False:
        raise RuntimeError(f"Failed to close {ticket} as Not a Bug")


# ---- Task creation ----


def parse_created_issue_key(text: str) -> str:
    matches = CREATED_TICKET_RE.findall(text)
    keys = [a or b for a, b in matches]
    if not keys:
        raise RuntimeError(f"Could not parse created HUM ticket from:\n{text}")
    return keys[-1]


def create_hum_task(
    summary: str,
    *,
    blocks: list[str],
    assignee: str = "",
    components: str = "A2: CVE & Scanners",
) -> str:
    ensure_rhjira_available()
    if not assignee:
        auth = load_jira_auth()
        assignee = auth.basic_auth_user or ""
    if not assignee:
        raise ValueError("Pass --assignee; JIRA_EMAIL is not set")
    create = run_rhjira(
        [
            "create",
            "--noeditor",
            "--project",
            "HUM",
            "--tickettype",
            "Task",
            "--summary",
            summary,
            "--assignee",
            assignee,
            "--components",
            components,
        ]
    )
    if create.returncode != 0:
        raise RuntimeError(
            (create.stderr or create.stdout or "rhjira create failed").strip()
        )
    key = parse_created_issue_key(f"{create.stdout}\n{create.stderr}")
    for tracker in blocks:
        link = run_rhjira(
            ["edit", key, "--noeditor", "--blocks", normalize_hum_key(tracker)]
        )
        if link.returncode != 0:
            raise RuntimeError(
                (link.stderr or link.stdout or f"failed to link {tracker}").strip()
            )
    status = run_rhjira(["edit", key, "--noeditor", "--status", "In Progress"])
    if status.returncode != 0:
        raise RuntimeError(
            (status.stderr or status.stdout or "failed to set In Progress").strip()
        )
    return key
