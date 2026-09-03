import re
from dataclasses import dataclass


# ---- Shared regexes ----

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,8}")
HUM_RE = re.compile(r"HUM-\d{3,6}")
MR_URL_RE = re.compile(
    r"https://gitlab\.com/redhat/hummingbird/rpms/-/merge_requests/\d+"
)

# ---- Repository constants ----

GITLAB_RPMS_REPO = "redhat/hummingbird/rpms"


# ---- Package guess extraction (used in TicketReport.__init__) ----


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


# ---- Dataclasses ----


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

    @classmethod
    def from_dict(cls, d: dict) -> "TicketReport":
        inst = cls.__new__(cls)
        inst.__dict__.update(d)
        return inst


# ---- Gathered-ticket container ----


@dataclass
class GatheredTickets:
    reports: list[TicketReport]
    user_provided: list[str]
    discovered: list[str]
