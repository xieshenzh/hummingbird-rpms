import json
import re
from typing import Any

from lib.models import SpecDepHit
from lib.utils import repo_root, _pkg_dir, normalize_nvr, extract_vendor_field


# ---- Constants ----

BUNDLED_PROVIDES_RE = re.compile(
    r"(?i)^\s*Provides:\s*bundled\(([^)]+)\)(?:\s*=\s*(\S+))?"
)
_MACRO_BODY = r"([^{}]*(?:\{[^{}]*\}[^{}]*)*)"  # body allowing one level of nested {}
_MAX_MACRO_EXPANSIONS = 10


# ---- Spec macro expansion ----


def _expand_macros(value: str, macros: dict[str, str]) -> str:
    for _ in range(_MAX_MACRO_EXPANSIONS):
        prev = value
        # %{!?name:body} — body if name NOT defined
        value = re.sub(
            r"%\{!\?(\w+):" + _MACRO_BODY + r"\}",
            lambda m: "" if m.group(1) in macros else m.group(2),
            value,
        )
        # %{?name:body} — body if name IS defined
        value = re.sub(
            r"%\{\?(\w+):" + _MACRO_BODY + r"\}",
            lambda m: m.group(2) if m.group(1) in macros else "",
            value,
        )
        # %{?name} — value if defined, else empty
        value = re.sub(r"%\{\?(\w+)\}", lambda m: macros.get(m.group(1), ""), value)
        # %{name} — simple expansion if defined, else leave literal
        value = re.sub(
            r"%\{(\w+)\}", lambda m: macros.get(m.group(1), m.group(0)), value
        )
        if value == prev:
            break
    return value


# ---- Spec NVR reading ----


def read_local_spec_nvr(package: str) -> str:
    spec_path = _pkg_dir(package) / f"{package}.spec"
    if not spec_path.is_file():
        return ""
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    # Only read the preamble — stop at the first section header
    preamble = re.split(
        r"^%(?:description|package|prep|build|install|files|changelog)\b",
        text,
        maxsplit=1,
        flags=re.MULTILINE,
    )[0]
    macros: dict[str, str] = {}
    for m in re.finditer(
        r"^%(?:global|define)\s+(\w+)\s+(.+)$", preamble, re.MULTILINE
    ):
        macros[m.group(1)] = m.group(2).strip()

    def get_field(field: str) -> str:
        m = re.search(rf"^{field}:\s*(.+)$", preamble, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    name = _expand_macros(get_field("Name"), macros)
    version = _expand_macros(get_field("Version"), macros)
    if not name or not version:
        return ""
    macros["version"] = version
    release = _expand_macros(get_field("Release"), macros)
    return f"{name}-{version}-{release}" if release else f"{name}-{version}"


def read_spec_release_before_dist(package: str) -> tuple[str, bool]:
    """Return (Release value before %{?dist}, uses_autorelease)."""
    spec_path = _pkg_dir(package) / f"{package}.spec"
    if not spec_path.is_file():
        raise RuntimeError(f"Spec not found: {spec_path}")
    text = spec_path.read_text(encoding="utf-8", errors="replace")
    preamble = re.split(
        r"^%(?:description|package|prep|build|install|files|changelog)\b",
        text,
        maxsplit=1,
        flags=re.MULTILINE,
    )[0]
    match = re.search(r"^Release:\s*(.+)$", preamble, re.MULTILINE | re.IGNORECASE)
    if not match:
        raise RuntimeError(f"No Release: line in {spec_path}")
    raw = match.group(1).strip()
    uses_autorelease = "%autorelease" in raw.lower() or "%{autorelease}" in raw.lower()
    dist_match = re.search(r"^(.*)%\{\??dist\}(.*)$", raw)
    before = dist_match.group(1).strip() if dist_match else raw
    return before, uses_autorelease


# ---- CVE reference search in spec and patches ----


def search_cve_in_spec_and_patches(package: str, cve_ids: list[str]) -> bool:
    pkg_dir = _pkg_dir(package)
    if not pkg_dir.is_dir() or not cve_ids:
        return False
    pattern = re.compile("|".join(re.escape(c) for c in cve_ids), re.IGNORECASE)
    files = [pkg_dir / f"{package}.spec"] + sorted(pkg_dir.glob("*.patch"))
    for path in files:
        if path.is_file() and pattern.search(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            return True
    return False


# ---- Bundled dependency probe ----


def probe_spec_deps(package: str, component: str = "") -> list[SpecDepHit]:
    spec_path = _pkg_dir(package) / f"{package}.spec"
    if not spec_path.is_file():
        raise RuntimeError(f"Spec not found: {spec_path}")
    hits: list[SpecDepHit] = []
    lines = spec_path.read_text(encoding="utf-8", errors="replace").splitlines()
    component_re = (
        re.compile(re.escape(component), re.IGNORECASE) if component else None
    )
    for idx, line in enumerate(lines, start=1):
        bundled = BUNDLED_PROVIDES_RE.search(line)
        if bundled:
            name, version = bundled.group(1), bundled.group(2) or ""
            if (
                component_re is None
                or component_re.search(name)
                or component_re.search(line)
            ):
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
    spec_path = _pkg_dir(package) / f"{package}.spec"
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


# ---- Version utilities ----


def split_nvr(nvr: str) -> tuple[str, str, str]:
    """Split an NVR into (name, version, release). Release may be empty."""
    cleaned = normalize_nvr(nvr)
    if not cleaned:
        return "", "", ""
    parts = cleaned.rsplit("-", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return cleaned, "", ""


def parse_version_key(value: str) -> tuple[int, ...] | None:
    """Numeric tuple from a dotted version; None if there are no digits."""
    text = (value or "").strip().lstrip("vV")
    parts = re.findall(r"\d+", text)
    if not parts:
        return None
    return tuple(int(p) for p in parts)


def version_cmp(left: str, right: str) -> int | None:
    """Return -1/0/1, or None when either side is not a dotted version."""
    left_key = parse_version_key(left)
    right_key = parse_version_key(right)
    if left_key is None or right_key is None:
        return None
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def parse_affected_constraints(affected: str) -> list[tuple[str, str]]:
    """Parse a CVE affected-range string into (op, version) constraints."""
    text = (affected or "").strip()
    if not text:
        return []
    constraints: list[tuple[str, str]] = []
    span_re = re.compile(r"(?i)(v?\d[\w.*+-]*)\s+through\s+(v?\d[\w.*+-]*)")
    for match in span_re.finditer(text):
        constraints.append((">=", match.group(1)))
        constraints.append(("<=", match.group(2)))
    text = span_re.sub(" ", text)
    op_re = re.compile(
        r"(?i)(?:^|[\s,])(<=|>=|<|>|=|before|prior\s+to|through)\s+(v?\d[\w.*+-]*)"
    )
    mapped = {
        "before": "<",
        "prior to": "<",
        "through": "<=",
    }
    for match in op_re.finditer(text):
        raw_op = re.sub(r"\s+", " ", match.group(1).lower())
        constraints.append((mapped.get(raw_op, raw_op), match.group(2)))
    return constraints


def version_satisfies(version: str, constraints: list[tuple[str, str]]) -> bool | None:
    """True if version matches every constraint; None if any side is incomparable."""
    if not constraints:
        return None
    ops = {
        "<": lambda cmp_val: cmp_val < 0,
        "<=": lambda cmp_val: cmp_val <= 0,
        ">": lambda cmp_val: cmp_val > 0,
        ">=": lambda cmp_val: cmp_val >= 0,
        "=": lambda cmp_val: cmp_val == 0,
    }
    for op, bound in constraints:
        pred = ops.get(op)
        if pred is None:
            return None
        cmp_val = version_cmp(version, bound)
        if cmp_val is None:
            return None
        if not pred(cmp_val):
            return False
    return True


def extract_analysis_nvr(block: str) -> str:
    if not block:
        return ""
    for field in (
        "Hummingbird SRPM version",
        "Hummingbird NVR",
        "NVR",
    ):
        value = extract_vendor_field(block, field)
        if value:
            return normalize_nvr(value)
    match = re.search(
        r"\b([A-Za-z0-9+_.-]+-\d[\w.+]*-\d[\w.+]*)(?:\.src\.rpm)?\b",
        block,
    )
    return normalize_nvr(match.group(1)) if match else ""


# ---- Version check payload ----


def version_check_payload(
    package: str,
    *,
    local_nvr: str = "",
    fib: str = "",
    affected: str = "",
    fixed: str = "",
    analysis_nvr: str = "",
) -> dict[str, Any]:
    if not local_nvr:
        local_nvr = read_local_spec_nvr(package)
    _name, local_version, _rel = split_nvr(local_nvr) if local_nvr else ("", "", "")
    fib_nvr = normalize_nvr(fib) if fib else ""
    _fib_name, fib_version, _fib_rel = split_nvr(fib_nvr) if fib_nvr else ("", "", "")
    analysis_nvr = normalize_nvr(analysis_nvr) if analysis_nvr else ""

    vs_fixed = ""
    if local_version and fixed:
        cmp_val = version_cmp(local_version, fixed)
        if cmp_val is None:
            vs_fixed = "unknown"
        elif cmp_val < 0:
            vs_fixed = "below"
        else:
            vs_fixed = "at_or_above"

    vs_affected = ""
    constraints = parse_affected_constraints(affected)
    if local_version and constraints:
        matched = version_satisfies(local_version, constraints)
        if matched is None:
            vs_affected = "unknown"
        elif matched:
            vs_affected = "in_range"
        else:
            vs_affected = "not_in_range"

    if vs_fixed == "at_or_above" and vs_affected == "in_range":
        hint = "conflicting_signals"
    elif vs_fixed == "at_or_above":
        hint = "possibly_at_or_above_fix"
    elif vs_fixed == "below" or vs_affected == "in_range":
        hint = "possibly_below_fix"
    elif vs_affected == "not_in_range":
        hint = "possibly_not_in_affected_range"
    else:
        hint = "unknown"

    note = (
        "Do not conclude Done-Errata from versions alone; "
        "use upstream-fix-age for the fix commit vs tag date."
    )
    if hint == "conflicting_signals":
        note = (
            "vs_fixed is at_or_above but vs_affected is still in_range "
            "(broad CVE range that includes the fix version). Do not treat "
            "this as below-fix; use upstream-fix-age before Done-Errata."
        )

    return {
        "package": package,
        "local_nvr": local_nvr,
        "local_version": local_version,
        "fib": fib_nvr,
        "fib_version": fib_version,
        "analysis_nvr": analysis_nvr,
        "affected": affected,
        "fixed": fixed,
        "vs_fixed": vs_fixed,
        "vs_affected": vs_affected,
        "hint": hint,
        "note": note,
    }


# ---- Release bump ----


def bump_release(current_release: str, upstream_release: str | None = None) -> str:
    """Bump a spec Release using the .N suffix. Keep in sync with ci/dist_git.py."""
    if upstream_release and current_release == upstream_release:
        return f"{current_release}.1"
    match = re.match(r"^(.+)\.(\d+)$", current_release)
    if match:
        base, num = match.groups()
        return f"{base}.{int(num) + 1}"
    return f"{current_release}.1"


def read_package_metadata(package: str) -> dict[str, Any]:
    path = repo_root() / "metadata" / f"{package}.json"
    if not path.is_file():
        raise RuntimeError(f"Metadata not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON in {path}: {err}") from err
    if not isinstance(data, dict):
        raise RuntimeError(f"Metadata {path} is not an object")
    return data


def release_bump_payload(package: str) -> dict[str, Any]:
    metadata = read_package_metadata(package)
    metadata_release = str(metadata.get("release") or "")
    spec_release, uses_autorelease = read_spec_release_before_dist(package)
    proposed = ""
    if uses_autorelease:
        hint = (
            "spec uses %autorelease; resolve it before a .N bump "
            "(see documentation/operating/rebuilding-packages.md)"
        )
    elif not spec_release:
        hint = "could not parse spec Release:"
    else:
        proposed = bump_release(spec_release, metadata_release or None)
        hint = "print only; do not write the spec or metadata release"
    return {
        "package": package,
        "modification_status": metadata.get("modification_status") or "",
        "metadata_release": metadata_release,
        "spec_release": spec_release,
        "uses_autorelease": uses_autorelease,
        "proposed_spec_release": proposed,
        "writes": False,
        "hint": hint,
        "note": (
            "Do not change metadata release for backports or rebuilds; "
            "only the spec Release: line gets the .N suffix."
        ),
    }


def apply_release_bump(package: str) -> dict[str, Any]:
    """Calculate and write the bumped Release line into the spec file."""
    payload = release_bump_payload(package)
    proposed = payload.get("proposed_spec_release")
    if not proposed or payload.get("uses_autorelease"):
        return payload
    spec_path = _pkg_dir(package) / f"{package}.spec"
    if not spec_path.is_file():
        return payload
    text = spec_path.read_text(encoding="utf-8")

    def replace_release(m: re.Match) -> str:
        line = m.group(0)
        pct_idx = line.find("%")
        if pct_idx != -1:
            dist_part = line[pct_idx:]
            return f"Release:        {proposed}{dist_part}"
        return f"Release:        {proposed}"

    new_text, count = re.subn(
        r"^Release:\s*.+$", replace_release, text, count=1, flags=re.MULTILINE | re.IGNORECASE
    )
    if count:
        spec_path.write_text(new_text, encoding="utf-8")
        payload["writes"] = True
        payload["hint"] = f"updated {spec_path.name} Release to {proposed}"
    return payload

