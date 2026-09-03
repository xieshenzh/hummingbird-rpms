"""Tests that every RPM package has upstream_repo in its metadata."""

import json
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

MISSING_FIELD_MESSAGE = """\
Package '{package}' is missing the 'upstream_repo' field in metadata/{package}.json.

Every package must have an 'upstream_repo' entry pointing to its canonical
upstream git repository.

To fix this, add the field to metadata/{package}.json:

    "upstream_repo": "https://github.com/example/project"

Optional fields you may also set:

    "upstream_branch": "<branch>"          # for versioned packages sharing a repo
    "cve_product": "<Vendor / Product>"    # or ["<Vendor / Product>", "<Vendor2 / Product2>"]
    "version_transform": "<rule>"          # version transform rule (defined and declared in tools repo)
    "upstream_version_transform": "<rule>"  # Anitya-to-RPM version transform (e.g., "openjdk_to_rpm")

If no upstream git repository exists, use the Fedora DistGit URL as a fallback:

    "upstream_repo": "https://src.fedoraproject.org/rpms/{package}"
"""


def _rpm_package_names() -> list[str]:
    """Return sorted list of RPM package directory names."""
    if not RPMS_DIR.is_dir():
        pytest.skip(f"rpms/ directory not found at {RPMS_DIR}")
    return sorted(d.name for d in RPMS_DIR.iterdir() if d.is_dir())


def _load_metadata(package: str) -> dict | None:
    """Load metadata JSON for a package, or None if missing."""
    path = METADATA_DIR / f"{package}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        pytest.fail(f"metadata/{package}.json contains invalid JSON: {e}")


@pytest.mark.parametrize("package", _rpm_package_names())
def test_upstream_repo_exists(package: str) -> None:
    """Each RPM package must have upstream_repo set in its metadata."""
    metadata = _load_metadata(package)
    assert metadata is not None, (
        f"Package '{package}' has no metadata file at metadata/{package}.json. "
        f"Create the file with at least: "
        f'{{"upstream_repo": "https://github.com/example/project", '
        f'"version": "...", "modification_status": "independent"}}'
    )
    val = metadata.get("upstream_repo")
    assert val is not None, MISSING_FIELD_MESSAGE.format(package=package)
    assert val != "", (
        f"Package '{package}' has an empty 'upstream_repo' value in "
        f"metadata/{package}.json. Set it to the canonical upstream git "
        f"repository URL, or use "
        f"'https://src.fedoraproject.org/rpms/{package}' as a fallback."
    )
