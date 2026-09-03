from typing import Any

from lib.models import TicketReport, SbomHit
from lib.utils import uses_vendored_deps
from lib.jira import extract_vendor_field, extract_flaw_summary
from lib.spec import read_local_spec_nvr, search_cve_in_spec_and_patches


# ---- Formatting templates ----

TICKET_HEADER_FMT = "{ticket}{pkg} {ttype} {status} | {sev} | {fib} | {assignee}{cves}"
TICKET_DETAIL = {
    "assessment": "  Assessment: {value}",
    "affected": "  Affected: {value}",
    "fixed": "  Fixed: {value}",
    "mr": "  MR: {value}",
    "link": "  Link: {ticket} [{ttype}] {status} - {summary}",
    "link_error": "  Link: {ticket} ERROR {error}",
    "component": "  Upstream component: {value}",
    "vendored": "  Vendored deps: yes (go-vendor-tools)",
    "local_nvr": "  Local: {value}",
    "cve_in_spec": "  CVE ref in spec/patches: {value}",
    "analysis_component": "  Component: {value}",
}


# ---- Ticket printing ----


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
        print(TICKET_DETAIL["assessment"].format(value=report.assessment))
    if report.affected_range or report.fixed_version:
        parts = []
        if report.affected_range:
            parts.append(TICKET_DETAIL["affected"].format(value=report.affected_range))
        if report.fixed_version:
            parts.append(TICKET_DETAIL["fixed"].format(value=report.fixed_version))
        print("  ".join(parts))
    for mr in report.mr_links_in_ticket:
        print(TICKET_DETAIL["mr"].format(value=mr))
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
        print(TICKET_DETAIL["component"].format(value=report.upstream_component))
    if report.package_guess and uses_vendored_deps(report.package_guess):
        print(TICKET_DETAIL["vendored"])
    if report.package_guess:
        nvr = read_local_spec_nvr(report.package_guess)
        if nvr:
            print(TICKET_DETAIL["local_nvr"].format(value=nvr))
        if report.cve_ids:
            found = search_cve_in_spec_and_patches(report.package_guess, report.cve_ids)
            print(TICKET_DETAIL["cve_in_spec"].format(value="yes" if found else "no"))
    if report.cve_analysis_block:
        comp = extract_vendor_field(report.cve_analysis_block, "Component")
        if comp:
            print(TICKET_DETAIL["analysis_component"].format(value=comp))
    if report.description:
        if flaw := extract_flaw_summary(report.description):
            print()
            print(flaw)
    if report.cve_analysis_block:
        print()
        print(report.cve_analysis_block)


# ---- Chat title helpers ----


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


# ---- SBOM output ----


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
