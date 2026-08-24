"""Jira wiki comment templates for /cve resolution comments."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lib.utils import normalize_hum_key


def _comment_text(message: str, file: Path | None) -> str:
    if file is not None:
        return file.read_text(encoding="utf-8").rstrip() + "\n"
    if message:
        return message.rstrip() + "\n"
    raise ValueError("Provide --message or --file")


COMMENT_KINDS = ("nab", "fib", "analysis", "next-release")
COMMENT_FIELD_NAMES = (
    "package",
    "component",
    "vex",
    "nvr",
    "affected",
    "fixed",
    "cve",
    "assessment",
    "sbom_source",
    "sbom_match",
    "runtime_installed",
    "spec_deps",
    "commit",
    "commit_date",
    "tag",
    "tag_date",
    "notes",
)
NAB_VEX_BLURB = {
    "Component not Present": (
        "The CVE product/component is not present in this Hummingbird package."
    ),
    "Vulnerable Code not Present": (
        "The package is a different product, or the vulnerable code path is not used."
    ),
}
_JSON_FIELD_ALIASES = {
    "nvr": ("nvr", "local_nvr", "fib"),
    "package": ("package", "package_guess"),
    "affected": ("affected", "affected_range"),
    "fixed": ("fixed", "fixed_version"),
    "component": ("component", "upstream_component"),
    "cve": ("cve", "cve_id"),
    "assessment": ("assessment", "hint", "verdict"),
    "commit": ("commit",),
    "commit_date": ("commit_date",),
    "tag": ("tag",),
    "tag_date": ("tag_date",),
    "sbom_source": ("sbom_source", "source"),
    "sbom_match": ("sbom_match",),
    "runtime_installed": ("runtime_installed",),
    "spec_deps": ("spec_deps",),
    "vex": ("vex",),
    "notes": ("notes",),
}


def empty_comment_fields() -> dict[str, str]:
    return {name: "" for name in COMMENT_FIELD_NAMES}


def wiki_mono(value: str) -> str:
    """Wrap a value in Jira wiki monospace, stripping nested {{ }}."""
    text = (value or "").replace("{{", "").replace("}}", "")
    return "{{" + text + "}}"


def _wiki_field(label: str, value: str, *, mono: bool = True) -> str | None:
    if not (value or "").strip():
        return None
    shown = wiki_mono(value.strip()) if mono else value.strip()
    return f"*{label}:* {shown}"


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, list) and value:
            parts = [str(item).strip() for item in value if str(item).strip()]
            if parts:
                return ", ".join(parts)
    return ""


def overlay_comment_fields(
    base: dict[str, str], overlay: dict[str, str]
) -> dict[str, str]:
    merged = empty_comment_fields()
    merged.update({key: value for key, value in base.items() if key in merged})
    for key, value in overlay.items():
        if key in merged and (value or "").strip():
            merged[key] = value.strip()
    return merged


def _fields_from_mapping(source: dict[str, Any]) -> dict[str, str]:
    fields = empty_comment_fields()
    nested = source.get("fields")
    if isinstance(nested, dict):
        fields = overlay_comment_fields(fields, _fields_from_mapping(nested))
    for dest, aliases in _JSON_FIELD_ALIASES.items():
        if fields[dest]:
            continue
        fields[dest] = _first_str(*(source.get(name) for name in aliases))
    return fields


def _fields_from_ticket_dict(ticket: dict[str, Any]) -> dict[str, str]:
    fields = _fields_from_mapping(ticket)
    if not fields["package"]:
        fields["package"] = _first_str(ticket.get("package_guess"))
    if not fields["nvr"]:
        fields["nvr"] = _first_str(ticket.get("fixed_in_build"))
    if not fields["cve"]:
        fields["cve"] = _first_str(ticket.get("cve_ids"))
    return fields


def _fields_from_probes(probes: dict[str, Any], package: str) -> dict[str, str]:
    fields = empty_comment_fields()
    if not package:
        return fields
    sbom_map = probes.get("sbom") or {}
    if package in sbom_map:
        sbom = sbom_map.get(package) or {}
        if isinstance(sbom, dict) and "error" not in sbom:
            source = _first_str(sbom.get("source"), sbom.get("url"), sbom.get("path"))
            if source:
                fields["sbom_source"] = source
            hits = sbom.get("hits")
            if isinstance(hits, list):
                if hits:
                    fields["sbom_match"] = f"{len(hits)} hit(s)"
                    scopes = sorted(
                        {
                            str(hit.get("scope")).strip()
                            for hit in hits
                            if isinstance(hit, dict)
                            and str(hit.get("scope") or "").strip()
                        }
                    )
                    if scopes:
                        fields["runtime_installed"] = ", ".join(scopes)
                else:
                    fields["sbom_match"] = "no hits"
    spec_map = probes.get("spec_deps") or {}
    if package in spec_map:
        spec_hits = spec_map.get(package) or []
        if isinstance(spec_hits, list) and spec_hits:
            fields["spec_deps"] = f"{len(spec_hits)} spec-deps hit(s)"
        elif isinstance(spec_hits, list):
            fields["spec_deps"] = "no spec-deps hits"
    return fields


def comment_fields_from_payload(
    data: dict[str, Any], *, ticket: str = ""
) -> dict[str, str]:
    """Map investigate / version-check / upstream-fix-age / flat JSON to fields."""
    fields = _fields_from_mapping(data)
    tickets = data.get("tickets")
    chosen: dict[str, Any] | None = None
    if isinstance(tickets, list) and tickets:
        wanted = ""
        if ticket:
            wanted = normalize_hum_key(ticket)
        user_provided = data.get("user_provided") or []
        if wanted:
            for entry in tickets:
                if not isinstance(entry, dict):
                    continue
                key = str(entry.get("ticket") or "")
                if key and normalize_hum_key(key) == wanted:
                    chosen = entry
                    break
        if chosen is None and user_provided:
            wanted_user = {
                normalize_hum_key(str(key))
                for key in user_provided
                if str(key).strip()
            }
            for entry in tickets:
                if not isinstance(entry, dict):
                    continue
                key = str(entry.get("ticket") or "")
                if key and normalize_hum_key(key) in wanted_user:
                    chosen = entry
                    break
        if chosen is None:
            first = tickets[0]
            chosen = first if isinstance(first, dict) else None
        if chosen is not None:
            fields = overlay_comment_fields(fields, _fields_from_ticket_dict(chosen))
    package = fields["package"]
    probes = data.get("probes")
    if isinstance(probes, dict):
        fields = overlay_comment_fields(fields, _fields_from_probes(probes, package))
    return fields


def load_comment_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise RuntimeError(f"Failed to read --from-json {path}: {err}") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"--from-json {path} must be a JSON object")
    return data


def comment_fields_from_args(args: argparse.Namespace) -> dict[str, str]:
    fields = empty_comment_fields()
    for name in COMMENT_FIELD_NAMES:
        if name == "notes":
            continue
        fields[name] = str(getattr(args, name, "") or "").strip()
    return fields


def validate_comment_fields(kind: str, fields: dict[str, str]) -> None:
    if kind not in COMMENT_KINDS:
        raise RuntimeError(
            f"Unknown comment kind {kind!r}; expected one of {', '.join(COMMENT_KINDS)}"
        )
    if kind == "nab":
        if not fields.get("vex"):
            raise RuntimeError("comment --kind nab needs --vex")
        if fields["vex"] not in NAB_VEX_BLURB:
            raise RuntimeError(
                "comment --kind nab --vex must be one of: "
                f"{', '.join(sorted(NAB_VEX_BLURB))!r}"
            )
        if not fields.get("package") and not fields.get("component"):
            raise RuntimeError("comment --kind nab needs --package or --component")
        return
    if kind == "fib":
        if not fields.get("package") or not fields.get("nvr"):
            raise RuntimeError("comment --kind fib needs --package and --nvr")
        return
    if kind == "analysis":
        if not fields.get("package"):
            raise RuntimeError("comment --kind analysis needs --package")
        return
    if not fields.get("package") and not fields.get("cve") and not fields.get("notes"):
        raise RuntimeError(
            "comment --kind next-release needs --package, --cve, or -m"
        )


def _join_wiki_lines(lines: list[str | None]) -> str:
    body = "\n".join(line for line in lines if line is not None)
    return body.rstrip() + "\n"


def _upstream_wiki_lines(fields: dict[str, str]) -> list[str | None]:
    commit = fields.get("commit") or ""
    commit_date = fields.get("commit_date") or ""
    tag = fields.get("tag") or ""
    tag_date = fields.get("tag_date") or ""
    commit_shown = commit
    if commit and commit_date:
        commit_shown = f"{commit} ({commit_date})"
    elif commit_date:
        commit_shown = commit_date
    tag_shown = tag
    if tag and tag_date:
        tag_shown = f"{tag} ({tag_date})"
    elif tag_date:
        tag_shown = tag_date
    return [
        _wiki_field("Upstream commit", commit_shown),
        _wiki_field("Upstream tag", tag_shown),
    ]


def render_comment(kind: str, fields: dict[str, str]) -> str:
    """Return Jira wiki markup for a CVE resolution comment."""
    validate_comment_fields(kind, fields)
    notes = (fields.get("notes") or "").strip()
    if kind == "nab":
        vex = fields["vex"]
        blurb = NAB_VEX_BLURB.get(vex, "Hummingbird is not affected.")
        evidence = [
            _wiki_field("Spec-deps", fields.get("spec_deps") or "", mono=False),
            _wiki_field("SBOM source", fields.get("sbom_source") or ""),
            _wiki_field("SBOM match", fields.get("sbom_match") or "", mono=False),
            _wiki_field(
                "Runtime-installed",
                fields.get("runtime_installed") or "",
                mono=False,
            ),
        ]
        evidence = [line for line in evidence if line]
        if not evidence:
            evidence = [
                (
                    "No SBOM/spec-deps fields passed; do not close without evidence "
                    "unless the user overrides."
                )
            ]
        lines = [
            "h3. Not a Bug",
            "",
            blurb,
            "",
            _wiki_field("Package", fields.get("package") or ""),
            _wiki_field("Component", fields.get("component") or ""),
            _wiki_field("VEX", vex, mono=False),
            _wiki_field("CVE", fields.get("cve") or ""),
            "",
            "h4. Evidence",
            "",
            *evidence,
        ]
        if notes:
            lines.extend(["", notes])
        return _join_wiki_lines(lines)
    if kind == "fib":
        nvr = fields["nvr"]
        lines = [
            "h3. Already fixed",
            "",
            (
                f"The local SRPM is at or above the fix. Setting Fixed in Build to "
                f"{wiki_mono(nvr)}. Leaving the tracker open for advisory automation."
            ),
            "",
            _wiki_field("Package", fields.get("package") or ""),
            _wiki_field("Local NVR", nvr),
            _wiki_field("CVE affected", fields.get("affected") or "", mono=False),
            _wiki_field("Fixed in", fields.get("fixed") or ""),
            _wiki_field("CVE", fields.get("cve") or ""),
            _wiki_field("Assessment", fields.get("assessment") or "", mono=False),
            *_upstream_wiki_lines(fields),
        ]
        if notes:
            lines.extend(["", notes])
        return _join_wiki_lines(lines)
    if kind == "analysis":
        lines = [
            "h3. Analysis",
            "",
            _wiki_field("Package", fields.get("package") or ""),
            _wiki_field("Local NVR", fields.get("nvr") or ""),
            _wiki_field("Assessment", fields.get("assessment") or "", mono=False),
            _wiki_field("Affected range", fields.get("affected") or "", mono=False),
            _wiki_field("Fixed in", fields.get("fixed") or ""),
            _wiki_field("CVE", fields.get("cve") or ""),
            *_upstream_wiki_lines(fields),
        ]
        if notes:
            lines.extend(["", notes])
        return _join_wiki_lines(lines)
    lines = [
        "h3. Waiting on upstream",
        "",
        (
            "Hummingbird is affected. No consumable upstream fix is available yet. "
            f"Labeling {wiki_mono('cve-next-release')} and leaving In Progress."
        ),
        "",
        _wiki_field("Package", fields.get("package") or ""),
        _wiki_field("CVE", fields.get("cve") or ""),
        _wiki_field("Affected range", fields.get("affected") or "", mono=False),
        _wiki_field("Assessment", fields.get("assessment") or "", mono=False),
    ]
    if notes:
        lines.extend(["", notes])
    return _join_wiki_lines(lines)


def resolve_comment_body(
    args: argparse.Namespace, *, default_kind: str = ""
) -> str:
    """Build a comment from --file, --kind, or --message."""
    if getattr(args, "comment_file", None) is not None:
        return _comment_text("", args.comment_file)
    kind = str(getattr(args, "kind", "") or "")
    message = str(getattr(args, "message", "") or "")
    if not kind and not message and default_kind:
        kind = default_kind
    if not kind:
        return _comment_text(message, None)
    fields = empty_comment_fields()
    from_json = getattr(args, "from_json", None)
    if from_json is not None:
        from_ticket = str(getattr(args, "from_ticket", "") or "")
        if not from_ticket:
            tickets = getattr(args, "tickets", None) or []
            if tickets:
                from_ticket = str(tickets[0])
        fields = comment_fields_from_payload(
            load_comment_json(Path(from_json)), ticket=from_ticket
        )
    fields = overlay_comment_fields(fields, comment_fields_from_args(args))
    if message.strip():
        extra = message.strip()
        fields["notes"] = (
            f"{fields['notes']}\n\n{extra}".strip() if fields["notes"] else extra
        )
    return render_comment(kind, fields)


def _emit_comment_body(args: argparse.Namespace, body: str, kind: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"kind": kind, "body": body}, indent=2, sort_keys=True))
        return
    if getattr(args, "print_only", False):
        sys.stdout.write(body if body.endswith("\n") else body + "\n")


def _skip_jira_write(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "print_only", False) or getattr(args, "json", False))



def _add_comment_template_arguments(
    parser: argparse.ArgumentParser, *, with_vex: bool = True
) -> None:
    parser.add_argument(
        "--kind",
        choices=COMMENT_KINDS,
        default="",
        help="Fill Jira wiki markup (nab, fib, analysis, next-release).",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        dest="from_json",
        help="Probe JSON from investigate, version-check, or upstream-fix-age.",
    )
    parser.add_argument(
        "--from-ticket",
        default="",
        dest="from_ticket",
        help="Ticket key to pick from investigate --json-out.",
    )
    parser.add_argument("--package", default="", help="SRPM package name.")
    parser.add_argument(
        "--component",
        default="",
        help="Upstream / CVE component name.",
    )
    if with_vex:
        parser.add_argument(
            "--vex",
            default="",
            choices=(*NAB_VEX_BLURB, ""),
            help="VEX justification (nab): Component not Present or "
            "Vulnerable Code not Present.",
        )
    parser.add_argument("--nvr", default="", help="Local or Fixed in Build NVR.")
    parser.add_argument("--affected", default="", help="CVE affected range.")
    parser.add_argument("--fixed", default="", help="CVE fixed version.")
    parser.add_argument("--cve", default="", help="CVE ID(s).")
    parser.add_argument(
        "--assessment",
        default="",
        help="Affected / not-affected assessment or version-check hint.",
    )
    parser.add_argument(
        "--sbom-source",
        default="",
        dest="sbom_source",
        help="SBOM origin (Jira attachment or Pulp URL/path).",
    )
    parser.add_argument(
        "--sbom-match",
        default="",
        dest="sbom_match",
        help="SBOM search result (no hits, build-time only, ...).",
    )
    parser.add_argument(
        "--runtime-installed",
        default="",
        dest="runtime_installed",
        help="Whether the component is runtime-installed (yes/no/scope).",
    )
    parser.add_argument(
        "--spec-deps",
        default="",
        dest="spec_deps",
        help="spec-deps evidence (no bundled reference, hit count, ...).",
    )
    parser.add_argument("--commit", default="", help="Upstream fix commit SHA or URL.")
    parser.add_argument(
        "--commit-date",
        default="",
        dest="commit_date",
        help="Fix commit date (ISO-8601).",
    )
    parser.add_argument("--tag", default="", help="Upstream tag or release name.")
    parser.add_argument(
        "--tag-date",
        default="",
        dest="tag_date",
        help="Tag/release date (ISO-8601).",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        dest="print_only",
        help="Print the body; do not write Jira.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON {kind, body} and do not write Jira.",
    )
