import json
import re
import sys
from pathlib import Path
from typing import Any

from lib.models import SbomHit
from lib.utils import http_get_text, http_download, normalize_hum_key
from lib.jira import run_rhjira


# ---- Constants ----

PULP_SBOM_BASE = (
    "https://packages.redhat.com/api/pulp-content/public-hummingbird/metadata/sboms"
)
SBOM_FILE_RE = re.compile(r"sha256-[a-fA-F0-9]+\.sbom")


# ---- Pulp SBOM discovery ----


def pulp_sbom_package_dir(package: str) -> str:
    """Pulp SBOM dirs replace dots in the package name with hyphens."""
    return f"{package.replace('.', '-')}-main"


def latest_pulp_sbom_filename(package: str) -> tuple[str, str]:
    """Return (directory_url, filename) for the latest Pulp SBOM object."""
    directory = f"{PULP_SBOM_BASE}/{pulp_sbom_package_dir(package)}/"
    listing = http_get_text(directory)
    files = sorted(set(SBOM_FILE_RE.findall(listing)))
    if not files:
        raise RuntimeError(f"No SBOM files found under {directory}")
    return directory, files[-1]


# ---- Jira attachment SBOM download ----


def try_download_jira_sbom(ticket: str, nvr: str, dest: Path) -> bool:
    """Download `{nvr}.sbom.json` from a Jira ticket attachment into dest."""
    attachment = f"{nvr}.sbom.json"
    workdir = dest.parent
    workdir.mkdir(parents=True, exist_ok=True)
    staged = workdir / attachment
    if staged.exists():
        staged.unlink()
    result = run_rhjira(["attach", "-d", ticket, attachment], cwd=workdir)
    if result.returncode != 0 or not staged.exists():
        return False
    if staged.resolve() != dest.resolve():
        dest.write_bytes(staged.read_bytes())
        if staged.exists() and staged.resolve() != dest.resolve():
            staged.unlink()
    return dest.exists()


def fetch_sbom(
    package: str,
    *,
    nvr: str = "",
    ticket: str = "",
    dest: Path | None = None,
) -> dict[str, Any]:
    """Fetch SBOM preferring a matching Jira attachment, else Pulp."""
    out = dest or Path(f"/tmp/{package}.sbom.json")
    out = out.expanduser()
    source = ""
    url = ""

    if ticket and nvr:
        ticket_key = normalize_hum_key(ticket)
        if try_download_jira_sbom(ticket_key, nvr, out):
            source = "jira"
            url = f"jira:{ticket_key}/{nvr}.sbom.json"
        else:
            print(
                f"WARN: Jira attachment {nvr}.sbom.json not found on {ticket_key}; "
                "falling back to Pulp",
                file=sys.stderr,
            )

    if not source:
        directory, filename = latest_pulp_sbom_filename(package)
        url = f"{directory}{filename}"
        http_download(url, out)
        source = "pulp"

    return {
        "package": package,
        "nvr": nvr,
        "ticket": ticket,
        "source": source,
        "url": url,
        "path": str(out),
        "bytes": out.stat().st_size if out.exists() else 0,
    }


# ---- SBOM search ----


def _component_fields(node: dict[str, Any]) -> dict[str, str]:
    name = str(node.get("name") or "")
    version = str(node.get("version") or "")
    purl = str(node.get("purl") or "")
    scope = str(node.get("scope") or "")
    ctype = str(node.get("type") or "")
    props = node.get("properties")
    if isinstance(props, list) and not scope:
        for prop in props:
            if not isinstance(prop, dict):
                continue
            key = str(prop.get("name") or prop.get("key") or "").lower()
            if "scope" in key or key.endswith("lifecycle"):
                scope = str(prop.get("value") or "")
                break
    return {
        "name": name,
        "version": version,
        "purl": purl,
        "scope": scope,
        "component_type": ctype,
    }


def search_sbom_file(path: Path, terms: list[str]) -> list[SbomHit]:
    """Search an SBOM for terms; prefer structured component hits, else text."""
    if not terms:
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hits: list[SbomHit] = []
    seen: set[tuple[str, str, str, str]] = set()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    def consider(term: str, node: dict[str, Any], json_path: str) -> None:
        fields = _component_fields(node)
        term_lower = term.lower()
        blob = " ".join(fields.values()).lower()
        if term_lower not in blob and term_lower not in json.dumps(node).lower():
            return
        key = (term, fields["name"], fields["version"], fields["purl"])
        if key in seen:
            return
        seen.add(key)
        hits.append(
            SbomHit(
                term=term,
                path=json_path,
                name=fields["name"],
                version=fields["version"],
                purl=fields["purl"],
                scope=fields["scope"],
                component_type=fields["component_type"],
                snippet="",
            )
        )

    def walk(node: Any, json_path: str = "$") -> None:
        if isinstance(node, dict):
            # CycloneDX component-like object
            if "name" in node or "purl" in node:
                for term in terms:
                    consider(term, node, json_path)
            for key, value in node.items():
                walk(value, f"{json_path}.{key}")
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, f"{json_path}[{idx}]")

    if data is not None:
        walk(data)

    if hits:
        return hits

    # Text fallback (non-JSON or no structured component matches)
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        count = 0
        for match in pattern.finditer(text):
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            snippet = text[start:end].replace("\n", " ")
            hits.append(SbomHit(term=term, path="text", snippet=snippet))
            count += 1
            if count >= 10:
                break
    return hits
