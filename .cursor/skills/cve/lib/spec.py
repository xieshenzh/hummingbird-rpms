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


_PRERELEASE_RE = re.compile(
    r"(?i)(\d)[-._~]?(dev|alpha|beta|preview|pre|rc)(?![a-zA-Z_])\.?(\d*)"
)
_PRERELEASE_RANK = {"dev": 0, "alpha": 1, "beta": 2, "pre": 3, "preview": 3, "rc": 4}


def parse_version_key(value: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """Comparable key for a dotted version; None if there are no digits.

    Returns (release_numbers, suffix) where suffix sorts a prerelease
    (rc/alpha/beta/dev/pre) below the corresponding final release, so
    2.5.0-rc1 compares lower than 2.5.0.
    """
    text = (value or "").strip().lstrip("vV")
    if not re.search(r"\d", text):
        return None
    pre = _PRERELEASE_RE.search(text)
    suffix: tuple[int, ...]
    if pre:
        release_text = text[: pre.start() + 1]
        rank = _PRERELEASE_RANK[pre.group(2).lower()]
        pre_num = int(pre.group(3)) if pre.group(3) else 0
        suffix = (0, rank, pre_num)
    else:
        release_text = text
        suffix = (1,)
    nums = tuple(int(p) for p in re.findall(r"\d+", release_text))
    if not nums:
        return None
    return (nums, suffix)


def version_major(value: str) -> int | None:
    """Return the leading numeric component of a version, or None."""
    key = parse_version_key(value)
    if key is None or not key[0]:
        return None
    return key[0][0]


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


_CVE_ID_RE = re.compile(r"(?i)^CVE-\d{4}-\d{4,8}$")


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
    for match in re.finditer(
        r"\b([A-Za-z0-9+_.-]+-\d[\w.+]*-\d[\w.+]*)(?:\.src\.rpm)?\b",
        block,
    ):
        candidate = match.group(1)
        # Do not treat a CVE id (CVE-2026-12345) as an NVR.
        if _CVE_ID_RE.match(candidate):
            continue
        return normalize_nvr(candidate)
    return ""


# ---- OR-separated affected branches / fixed versions ----


def split_or_branches(text: str) -> list[str]:
    """Split disjoint affected branches on `or`/`;` (keeps `,` within a branch)."""
    if not text:
        return []
    parts = re.split(r"(?i)\s+or\s+|;", text)
    return [p.strip() for p in parts if p.strip()]


def split_fixed_versions(text: str) -> list[str]:
    """Split a branch-specific fixed-version list on `,`/`;`/`or`/whitespace."""
    if not text:
        return []
    tokens = re.split(r"(?i)\s+or\s+|[,;\s]+", text)
    return [t.strip() for t in tokens if t.strip() and re.search(r"\d", t)]


def version_satisfies_any(
    version: str, branches: list[list[tuple[str, str]]]
) -> bool | None:
    """OR semantics: True if any branch is satisfied, None if all are unknown."""
    if not branches:
        return None
    any_unknown = False
    for constraints in branches:
        result = version_satisfies(version, constraints)
        if result is True:
            return True
        if result is None:
            any_unknown = True
    return None if any_unknown else False


def select_fixed_for_version(fixed: str, version: str) -> str:
    """Pick the branch-specific fixed version matching `version`'s major line."""
    versions = split_fixed_versions(fixed)
    if not versions:
        return ""
    if len(versions) == 1:
        return versions[0]
    target = version_major(version)
    for candidate in versions:
        if target is not None and version_major(candidate) == target:
            return candidate
    return ""


# ---- Component version resolution ----


def resolve_component_version(
    package: str,
    component: str,
    *,
    analysis_block: str = "",
) -> tuple[str, str]:
    """Resolve the affected component version and its evidence source.

    Order: matching bundled Provides in the spec, then the cve_analysis
    record. Returns ("", "") when no component version is found.
    """
    comp = (component or "").strip().lower()
    if not comp:
        return "", ""
    try:
        hits = probe_spec_deps(package, comp)
    except RuntimeError:
        hits = []
    for hit in hits:
        name = hit.bundled_name.lower()
        if hit.kind == "bundled_provides" and hit.bundled_version and comp in name:
            return hit.bundled_version, "spec_provides"
    for field in ("Component version", "Bundled version"):
        # Fuzzy: first whitespace/end-delimited version token in the field value.
        match = re.search(
            r"(?<!\w)v?(\d[\w.+~-]*)(?=\s|$)",
            extract_vendor_field(analysis_block, field),
        )
        if match:
            return match.group(1), "analysis"
    return "", ""


# ---- Version check payload ----


def version_check_payload(
    package: str,
    *,
    local_nvr: str = "",
    fib: str = "",
    affected: str = "",
    fixed: str = "",
    analysis_nvr: str = "",
    component: str = "",
    analysis_block: str = "",
) -> dict[str, Any]:
    if not local_nvr:
        local_nvr = read_local_spec_nvr(package)
    _name, local_version, _rel = split_nvr(local_nvr) if local_nvr else ("", "", "")
    fib_nvr = normalize_nvr(fib) if fib else ""
    _fib_name, fib_version, _fib_rel = split_nvr(fib_nvr) if fib_nvr else ("", "", "")
    analysis_nvr = normalize_nvr(analysis_nvr) if analysis_nvr else ""

    # Choose the version to compare: the affected component's version when a
    # component is named, otherwise the parent RPM version.
    component = (component or "").strip()
    compare_version = local_version
    version_source = "parent_rpm" if local_version else ""
    comparable = True
    if component:
        resolved, evidence = resolve_component_version(
            package,
            component,
            analysis_block=analysis_block,
        )
        if resolved:
            compare_version = resolved
            version_source = evidence
        else:
            # No component version resolvable: refuse to compare against the
            # parent RPM version (avoids e.g. grafana 13.x vs fast-uri 3.x).
            compare_version = ""
            version_source = ""
            comparable = False

    # Select the branch-specific fixed version matching the compared major line.
    selected_fixed = select_fixed_for_version(fixed, compare_version) if fixed else ""

    vs_fixed = ""
    if comparable and compare_version and selected_fixed:
        cmp_val = version_cmp(compare_version, selected_fixed)
        if cmp_val is None:
            vs_fixed = "unknown"
        elif cmp_val < 0:
            vs_fixed = "below"
        else:
            vs_fixed = "at_or_above"

    # OR-separated affected branches: version is affected if any branch matches.
    branches = [parse_affected_constraints(b) for b in split_or_branches(affected)]
    branches = [c for c in branches if c]
    vs_affected = ""
    if comparable and compare_version and branches:
        matched = version_satisfies_any(compare_version, branches)
        if matched is None:
            vs_affected = "unknown"
        elif matched:
            vs_affected = "in_range"
        else:
            vs_affected = "not_in_range"

    if not comparable:
        hint = "non_comparable"
    elif vs_fixed == "at_or_above" and vs_affected == "in_range":
        hint = "conflicting_signals"
    elif vs_fixed == "at_or_above":
        hint = "possibly_at_or_above_fix"
    elif vs_affected == "in_range" and not selected_fixed:
        # Affected, but no fix version exists for this major branch.
        hint = "in_range_no_fix_for_branch"
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
    if not comparable:
        note = (
            f"No version resolved for component '{component}'; "
            "not comparable. Refusing to compare the parent RPM version "
            "against a component-specific CVE range. Add a bundled Provides "
            "or confirm the component is not present."
        )
    elif hint == "conflicting_signals":
        note = (
            "vs_fixed is at_or_above but vs_affected is still in_range "
            "(broad CVE range that includes the fix version). Do not treat "
            "this as below-fix; use upstream-fix-age before Done-Errata."
        )

    return {
        "package": package,
        "local_nvr": local_nvr,
        "local_version": local_version,
        "component": component,
        "compare_version": compare_version,
        "version_source": version_source,
        "comparable": comparable,
        "fib": fib_nvr,
        "fib_version": fib_version,
        "analysis_nvr": analysis_nvr,
        "affected": affected,
        "fixed": fixed,
        "selected_fixed": selected_fixed,
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

