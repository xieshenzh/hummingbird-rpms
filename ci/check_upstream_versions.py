#!/usr/bin/env python3
"""
Check for available upstream version updates using release-monitoring.org.

This script queries release-monitoring.org (Anitya) to check if there are
newer versions available for RPM packages in the repository. It can also
update the spec files to the new version.

When --update is used, the default behaviour updates the spec via the
specfile library and downloads new sources from the URLs in the spec.
Packages that require custom logic can provide a hooks file at
``metadata/<package>.update-hooks.yaml`` with up to three phases
(update_spec, download_sources, post_update) that override or extend
the defaults. See the Package Modification Tracking documentation for
the full hook reference.

If ``metadata/<package>.source-pipeline.yaml`` exists, it takes priority
over both the hooks file and the generic default for source download:
the gorget source-pipeline tool (quay.io/hummingbird-ci/gorget) runs in a
container to fetch, transform, verify, and emit source artifacts, which
are then uploaded to the lookaside cache in place of the download_sources
phase. Pass --skip-pipeline to force the legacy hook/default path even
when a source-pipeline definition exists. See
documentation/design/source-pipeline-tool.md for background on gorget;
the pipeline YAML schema itself is defined by gorget's own
src/gorget/config/schema.py (github.com/gorget-project/gorget), which has
drifted ahead of the design doc's examples.

Subcommands:
    check   Check tracked packages for updates
    list    List ALL packages with version and tracking status

Usage:
    # Check tracked packages
    ./ci/check_upstream_versions.py check

    # Check specific packages
    ./ci/check_upstream_versions.py check curl openssl gnutls

    # Show all packages (including up-to-date ones)
    ./ci/check_upstream_versions.py check --all

    # Output as JSON
    ./ci/check_upstream_versions.py check --json

    # Update spec files to new versions
    ./ci/check_upstream_versions.py check --update curl gnutls

    # List all packages with status table
    ./ci/check_upstream_versions.py list

    # List all packages as JSON
    ./ci/check_upstream_versions.py list --json
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

# Try to import rpm for version comparison; fall back to packaging if unavailable
try:
    import rpm  # type: ignore[import-untyped]

    HAS_RPM = True
except ImportError:
    HAS_RPM = False
    from packaging.version import Version, InvalidVersion

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

# release-monitoring.org API base URL
ANITYA_API_BASE = "https://release-monitoring.org/api"
UPLOAD_SCRIPT = ROOT_DIR / "ci" / "upload-to-lookaside-cache.sh"

# Container image for the gorget source-pipeline tool (see
# metadata/<package>.source-pipeline.yaml and documentation/design/source-pipeline-tool.md)
# renovate: datasource=docker depName=quay.io/hummingbird-ci/gorget
GORGET_IMAGE = "quay.io/hummingbird-ci/gorget:latest@sha256:544625a977e612dfaca521ed9f48264ef42562e92e9d44de583c00791901d8d4"

# Rate limiting: delay between API requests (in seconds)
API_DELAY = 0.2

# Default distribution to look up packages
DEFAULT_DISTRO = "Fedora"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Global sign-off flag, set from command line
sign_off: bool = False

# Global escape hatch: force the legacy hook/default source-download path
# even when metadata/<package>.source-pipeline.yaml exists, set from --skip-pipeline
skip_pipeline: bool = False


def run_git(
    *args: str, cwd: Path | str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    command = ["git", *args]
    display_command = shlex.join(command)
    logger.info("Running command: %s", display_command)
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=cwd, check=True, stdout=subprocess.PIPE, text=True
        )
    except subprocess.CalledProcessError:
        logger.error(
            "Command failed after %.1fs: %s",
            time.monotonic() - started_at,
            display_command,
        )
        raise
    logger.info(
        "Command completed after %.1fs: %s",
        time.monotonic() - started_at,
        display_command,
    )
    return result


def run_git_commit(
    *args: str, cwd: Path | str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git commit with optional --signoff flag."""
    commit_args = ["commit"]
    if sign_off:
        commit_args.append("--signoff")
    commit_args.extend(args)
    return run_git(*commit_args, cwd=cwd)


@dataclass
class VersionCheckResult:
    """Result of checking a package's upstream version."""

    package: str
    current_version: str
    upstream_version: Optional[str]
    has_update: bool
    error: Optional[str] = None
    anitya_project_id: Optional[int] = None
    updated: bool = False
    update_error: Optional[str] = None
    downloaded_sources: Optional[list[str]] = None


def compare_versions(current: str, upstream: str) -> int:
    """
    Compare two version strings.

    Returns:
        1 if upstream is newer than current
        0 if versions are equal
        -1 if current is newer than upstream
    """
    if HAS_RPM:
        # Use RPM's labelCompare for accurate comparison
        # labelCompare takes (epoch, version, release) tuples
        # We use epoch 0 and release 1 as placeholders
        result = rpm.labelCompare(("0", current, "1"), ("0", upstream, "1"))
        # labelCompare returns: 1 if first > second, 0 if equal, -1 if first < second
        # We want: 1 if upstream > current, so invert the result
        return -result
    else:
        # Fall back to packaging library
        try:
            v_current = Version(current)
            v_upstream = Version(upstream)
            if v_upstream > v_current:
                return 1
            elif v_upstream == v_current:
                return 0
            else:
                return -1
        except InvalidVersion:
            # If versions can't be parsed, do string comparison
            if upstream > current:
                return 1
            elif upstream == current:
                return 0
            else:
                return -1


def parse_spec_version(package_dir: Path) -> Optional[str]:
    """Extract version from package's spec file using rpmspec."""
    result = parse_spec_version_release(package_dir)
    if result is None:
        return None
    return result[0]


def parse_spec_version_release(package_dir: Path) -> Optional[tuple[str, str]]:
    """Extract version and release from package's spec file using rpmspec.

    Returns:
        (version, release) tuple, or None if the spec cannot be parsed.
        The release has the dist suffix stripped (queried with dist set to nil).
    """
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        return None

    spec_file = spec_files[0]
    try:
        result = subprocess.run(
            [
                "rpmspec",
                "-q",
                "--qf",
                "%{VERSION}\n%{RELEASE}\n",
                "--define=dist %{nil}",
                f"--define=_sourcedir {package_dir}",
                "--srpm",
                str(spec_file),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return None
        version = lines[0]
        release = lines[1]
        return (version, release) if version else None
    except subprocess.CalledProcessError:
        return None


def get_package_metadata(package: str) -> Optional[dict]:
    """Get full metadata dict from metadata JSON file."""
    metadata_file = METADATA_DIR / f"{package}.json"
    if not metadata_file.exists():
        return None

    try:
        with open(metadata_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get_version_from_metadata(package: str) -> Optional[str]:
    """Get version from metadata JSON file."""
    data = get_package_metadata(package)
    if data is None:
        return None
    return data.get("version")


def _get_spec_sources(spec_path: str, sourcedir: str) -> dict[int, str]:
    """
    Get source locations from RPM's fully parsed view of a spec file.

    Sources can be generated by macros or Lua rather than appearing as literal
    ``SourceN:`` tags in the spec.  ``Specfile.sources()`` operates on the raw
    editable text and therefore cannot see those generated tags.  Build a
    read-only source collection from ``parsed_sections`` instead, after RPM has
    expanded macros and executed Lua.

    Returns a dict mapping source number to expanded URL/location.
    """
    from specfile import Specfile
    from specfile.sourcelist import Sourcelist
    from specfile.sources import Sources
    from specfile.tags import Tags

    spec = Specfile(spec_path, sourcedir=sourcedir)
    sections = spec.parsed_sections
    tags = Tags.parse(sections.package)
    sourcelists = [
        Sourcelist.parse(section)
        for section in sections
        if section.normalized_id == "sourcelist"
    ]

    # Locations in parsed_sections are already macro-expanded.
    return {src.number: src.location for src in Sources(tags, sourcelists)}


def _get_spec_source_urls(
    spec_path: str,
    sourcedir: str,
    sources: Optional[dict[int, str]] = None,
) -> dict[int, str]:
    """Return RPM-expanded HTTP, HTTPS, and FTP source locations."""
    if sources is None:
        sources = _get_spec_sources(spec_path, sourcedir)
    return {
        number: location
        for number, location in sources.items()
        if location.startswith(("http://", "https://", "ftp://"))
    }


def _parse_sources_file(sources_path: Path) -> list[dict]:
    """
    Parse a dist-git 'sources' file.

    Each line has format: ALGO (filename) = hash

    Returns a list of dicts with keys: algo, filename, hash, line.
    """
    entries: list[dict[str, str]] = []
    if not sources_path.exists():
        return entries

    for line in sources_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(\w+)\s+\((.+?)\)\s+=\s+(\w+)$", line)
        if match:
            entries.append(
                {
                    "algo": match.group(1),
                    "filename": match.group(2),
                    "hash": match.group(3),
                    "line": line,
                }
            )
    return entries


def _write_sources_file(sources_path: Path, entries: list[dict]) -> None:
    """Write entries back to a dist-git 'sources' file."""
    lines = [f"{e['algo']} ({e['filename']}) = {e['hash']}" for e in entries]
    sources_path.write_text("\n".join(lines) + "\n")


def _compute_file_hash(filepath: Path, algo: str = "SHA512") -> str:
    """Compute hash of a file."""
    h = hashlib.new(algo.lower())
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_file(url: str, dest: Path) -> None:
    """Download a file from a URL to a local path."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "hummingbird-rpms-version-checker/1.0"}
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        with open(dest, "wb") as f:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                f.write(chunk)


def _upload_to_lookaside(
    filepath: Path, package: str, hashtype: str = "sha512"
) -> None:
    """Upload a file to the lookaside cache using upload-to-lookaside-cache.sh."""
    if not UPLOAD_SCRIPT.exists():
        raise FileNotFoundError(f"Upload script not found: {UPLOAD_SCRIPT}")
    result = subprocess.run(
        [
            str(UPLOAD_SCRIPT),
            "-f",
            str(filepath),
            "-p",
            package,
            "-t",
            hashtype.lower(),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"Lookaside upload failed (exit {result.returncode}): {stderr}"
        )
    logger.info(f"{package}: uploaded {filepath.name} to lookaside cache")


def _regenerate_vendor_archive(
    package: str,
    old_version: str,
    new_version: str,
    sources_entries: list[dict],
) -> str | None:
    """
    Regenerate a go-vendor-tools vendor archive after a version update.

    If the package directory contains a ``go-vendor-tools.toml`` file and
    the sources file has a ``*-vendor.tar.*`` entry, regenerate the vendor
    archive using ``go_vendor_archive create``, upload it to the lookaside
    cache, and update *sources_entries* in place.

    Args:
        package: Package name
        old_version: Version before the spec update
        new_version: Version after the spec update
        sources_entries: Mutable list of source entry dicts (modified in place)

    Returns:
        The new vendor filename, or None if this package does not use
        go-vendor-tools.
    """
    package_dir = RPMS_DIR / package
    config_path = package_dir / "go-vendor-tools.toml"
    if not config_path.exists():
        return None

    # Find the vendor entry in sources
    vendor_entry = None
    for entry in sources_entries:
        if "-vendor.tar." in entry["filename"]:
            vendor_entry = entry
            break

    if vendor_entry is None:
        logger.debug(f"{package}: go-vendor-tools.toml exists but no vendor entry in sources")
        return None

    old_vendor_filename = vendor_entry["filename"]
    new_vendor_filename = old_vendor_filename.replace(old_version, new_version)

    # Find the spec file
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        logger.warning(f"{package}: expected one spec file, found {len(spec_files)}")
        return None

    # Run go_vendor_archive create
    logger.info(f"{package}: regenerating vendor archive {new_vendor_filename}")
    result = subprocess.run(
        [
            "go_vendor_archive",
            "create",
            "-c",
            str(config_path),
            str(spec_files[0]),
        ],
        cwd=str(package_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"go_vendor_archive create failed (exit {result.returncode}): {stderr}"
        )

    vendor_path = package_dir / new_vendor_filename
    if not vendor_path.exists():
        raise FileNotFoundError(
            f"go_vendor_archive did not produce {new_vendor_filename}"
        )

    # Upload to lookaside cache
    algo = vendor_entry["algo"]
    _upload_to_lookaside(vendor_path, package, algo)

    # Compute hash and update the sources entry in place
    new_hash = _compute_file_hash(vendor_path, algo)
    vendor_entry["filename"] = new_vendor_filename
    vendor_entry["hash"] = new_hash

    # Remove old vendor archive if the filename changed
    if old_vendor_filename != new_vendor_filename:
        old_vendor_path = package_dir / old_vendor_filename
        if old_vendor_path.exists():
            old_vendor_path.unlink()
            logger.debug(f"{package}: removed old vendor archive {old_vendor_filename}")

    return new_vendor_filename


def _load_source_pipeline(package: str) -> Optional[Path]:
    """Return the path to metadata/<package>.source-pipeline.yaml, or None.

    A source-pipeline definition takes priority over both
    metadata/<package>.update-hooks.yaml and the generic default source
    download path (see gorget's container interface,
    documentation/design/source-pipeline-tool.md).
    """
    pipeline_file = METADATA_DIR / f"{package}.source-pipeline.yaml"
    return pipeline_file if pipeline_file.is_file() else None


def _run_gorget_pipeline(
    package: str,
    old_version: str,
    new_version: str,
    pipeline_file: Path,
) -> list[str]:
    """
    Run the gorget source-pipeline tool for a package and upload its
    output to the lookaside cache.

    Invokes GORGET_IMAGE via podman, mounting the package directory
    (read-only), the pipeline YAML (read-only), a shared GPG keyring
    directory (read-only), and a scratch output directory. On success,
    every artifact gorget emits is copied into the package directory,
    uploaded to the lookaside cache, and the package's ``sources`` file
    is replaced wholesale with the one gorget emitted -- gorget's sources
    file is authoritative for a pipeline-managed package, not something to
    hand-patch entries into.

    Args:
        package: Package name
        old_version: Version before the spec update
        new_version: Version after the spec update
        pipeline_file: Path to metadata/<package>.source-pipeline.yaml

    Returns:
        List of new source filenames (for the caller to add to .gitignore)

    Raises:
        RuntimeError: If the gorget container exits non-zero, or emits no
            usable ``sources`` file
    """
    package_dir = RPMS_DIR / package
    gpg_keys_dir = METADATA_DIR / "gpg-keys"
    gpg_keys_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"gorget-{package}-") as output_dir_str:
        output_dir = Path(output_dir_str)
        output_dir.chmod(0o777)

        command = [
            "podman", "run", "--rm",
            "-v", f"{package_dir}:/package:ro,z",
            "-v", f"{pipeline_file}:/pipeline.yaml:ro,z",
            "-v", f"{gpg_keys_dir}:/gpg-keys:ro,z",
            "-v", f"{output_dir}:/output:z",
            GORGET_IMAGE,
            "--version", new_version,
            "--old-version", old_version,
        ]
        logger.info(
            "%s: running gorget pipeline %s -> %s: %s",
            package, old_version, new_version, shlex.join(command),
        )
        started_at = time.monotonic()
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, text=True)
        finally:
            logger.info(
                "%s: gorget pipeline completed after %.1fs",
                package, time.monotonic() - started_at,
            )

        if result.returncode != 0:
            report_path = output_dir / "report.json"
            report = f"\n{report_path.read_text()}" if report_path.is_file() else ""
            details = result.stdout.strip()
            suffix = f": {details}" if details else ""
            raise RuntimeError(
                f"{package}: gorget pipeline failed (exit {result.returncode}){suffix}{report}"
            )

        new_sources_path = output_dir / "sources"
        if not new_sources_path.is_file():
            raise RuntimeError(
                f"{package}: gorget pipeline exited 0 but emitted no 'sources' file"
            )
        new_entries = _parse_sources_file(new_sources_path)
        if not new_entries:
            raise RuntimeError(
                f"{package}: gorget pipeline emitted an empty 'sources' file "
                f"(no parseable entries)"
            )

        uploaded = []
        for entry in new_entries:
            src = output_dir / entry["filename"]
            if not src.is_file():
                raise RuntimeError(
                    f"{package}: gorget's sources file references "
                    f"{entry['filename']!r} but it was not emitted to /output"
                )
            dest = package_dir / entry["filename"]
            shutil.copyfile(src, dest)
            _upload_to_lookaside(dest, package, entry["algo"])
            uploaded.append(entry["filename"])

    # Remove local files superseded by this run (old filenames not in the
    # new gorget-emitted sources file).
    old_entries = _parse_sources_file(package_dir / "sources")
    old_filenames = {e["filename"] for e in old_entries}
    new_filenames = {e["filename"] for e in new_entries}
    for filename in sorted(old_filenames - new_filenames):
        stale_path = package_dir / filename
        if stale_path.exists():
            stale_path.unlink()
            logger.debug(f"{package}: removed stale source entry {filename}")

    _write_sources_file(package_dir / "sources", new_entries)
    return uploaded


def download_new_sources(
    package: str,
    old_version: str,
    new_version: str,
) -> list[str]:
    """
    Download new source archives after a version update.

    Compares the source URLs in the updated spec against the existing
    ``sources`` file.  Any source whose filename is not already present
    is downloaded and uploaded to the lookaside cache.  Stale entries
    (filenames in ``sources`` that no longer appear in the spec) are
    removed.

    Args:
        package: Package name
        old_version: Version before the spec update
        new_version: Version after the spec update

    Returns:
        List of downloaded filenames
    """
    package_dir = RPMS_DIR / package
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        return []

    sources_path = package_dir / "sources"
    sources_entries = _parse_sources_file(sources_path)
    sources_filenames = {e["filename"] for e in sources_entries}

    # The spec has already been updated to new_version at this point.
    # Get the new expanded source URLs from the updated spec.
    spec_sources = _get_spec_sources(str(spec_files[0]), str(package_dir))
    new_source_urls = _get_spec_source_urls(
        str(spec_files[0]), str(package_dir), spec_sources
    )
    spec_filenames = {os.path.basename(url) for url in new_source_urls.values()}

    if sources_entries and not new_source_urls:
        declared_filenames = {
            os.path.basename(location)
            for location in spec_sources.values()
        }
        unexplained_entries = sources_filenames - declared_filenames
        if unexplained_entries:
            raise RuntimeError(
                f"{package}: source discovery returned no remote URLs; "
                f"refusing to remove {len(unexplained_entries)} existing "
                f"source entries not declared in the spec"
            )
        logger.debug(
            f"{package}: spec contains only local sources; preserving "
            f"{len(sources_entries)} existing source entries"
        )
        return []

    downloaded = []
    for src_num, new_url in new_source_urls.items():
        new_filename = os.path.basename(new_url)

        if new_filename in sources_filenames:
            continue

        logger.debug(f"{package}: Source{src_num}: new source {new_filename}")

        dest = package_dir / new_filename
        logger.info(f"{package}: downloading {new_url}")
        _download_file(new_url, dest)

        _upload_to_lookaside(dest, package, "SHA512")

        new_hash = _compute_file_hash(dest, "SHA512")
        sources_entries.append(
            {"algo": "SHA512", "filename": new_filename, "hash": new_hash}
        )
        downloaded.append(new_filename)

    # Regenerate vendor archive for go-vendor-tools packages (must run
    # before stale-entry removal so the old vendor entry is still present).
    vendor_file = _regenerate_vendor_archive(
        package, old_version, new_version, sources_entries,
    )
    if vendor_file:
        downloaded.append(vendor_file)

    # Remove stale entries whose filenames no longer appear in the spec
    # or as a current vendor archive.
    current_filenames = spec_filenames | {
        e["filename"] for e in sources_entries if "-vendor.tar." in e["filename"]
    }
    stale = sources_filenames - current_filenames
    if stale:
        for filename in sorted(stale):
            logger.debug(f"{package}: removing stale source entry {filename}")
            old_file = package_dir / filename
            if old_file.exists():
                old_file.unlink()
        sources_entries = [
            e for e in sources_entries if e["filename"] not in stale
        ]

    if downloaded or stale:
        _write_sources_file(sources_path, sources_entries)

    return downloaded


def _update_gitignore(package_dir: Path, filenames: list[str]) -> None:
    """Add filenames to .gitignore in the package directory.

    Ensures that files uploaded to the lookaside cache are not
    tracked by git.
    """
    gitignore_path = package_dir / ".gitignore"

    existing_lines: set[str] = set()
    if gitignore_path.exists():
        existing_lines = set(gitignore_path.read_text().splitlines())

    new_entries = []
    for filename in filenames:
        entry = filename
        if entry not in existing_lines:
            new_entries.append(entry)

    if new_entries:
        with open(gitignore_path, "a") as f:
            for entry in new_entries:
                f.write(f"{entry}\n")
        logger.info(
            f"{package_dir.name}: added {len(new_entries)} "
            f"file(s) to .gitignore"
        )


def mark_package_modified(
    package: str,
    reason: str,
    version: Optional[str] = None,
    release: Optional[str] = None,
) -> None:
    """
    Mark a package's metadata as modified.

    Sets modification_status to "modified" to indicate the package was
    updated from an upstream release. This blocks automatic dist-git
    updates until the package is manually marked as clean.

    Args:
        package: Package name
        reason: Reason for the update
        version: If provided, update the version field in metadata
        release: If provided, update the release field in metadata
    """
    metadata_file = METADATA_DIR / f"{package}.json"
    if not metadata_file.exists():
        logger.warning(
            f"{package}: no metadata file found, skipping modification tracking"
        )
        return

    try:
        with open(metadata_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"{package}: failed to read metadata: {e}")
        return

    if data.get("modification_status") == "native":
        logger.info(f"{package}: native package, skipping modification tracking")
    else:
        data["modification_status"] = "modified"
        data["modification_reason"] = reason
    if version is not None:
        data["version"] = version
    if release is not None:
        data["release"] = release

    try:
        with open(metadata_file, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError as e:
        logger.error(f"{package}: failed to write metadata: {e}")
        return

    logger.info(f"{package}: marked as modified in metadata")


# ---------------------------------------------------------------------------
# Per-package update hooks
# ---------------------------------------------------------------------------

_VALID_HOOK_PHASES = frozenset({"update_spec", "download_sources", "post_update"})


@dataclass
class UpdateHooks:
    """Optional per-package hook commands loaded from update-hooks.yaml."""

    update_spec: Optional[str] = None
    download_sources: Optional[str] = None
    post_update: Optional[str] = None


def _load_update_hooks(package: str) -> Optional[UpdateHooks]:
    """Load update-hooks.yaml from a package directory.

    Returns an ``UpdateHooks`` instance if the file exists, or ``None``
    if no hooks file is present.

    Raises ``ValueError`` if the file contains unknown phase keys.
    """
    hooks_file = METADATA_DIR / f"{package}.update-hooks.yaml"
    if not hooks_file.exists():
        return None

    with open(hooks_file) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{hooks_file}: expected a YAML mapping, got {type(data).__name__}"
        )

    unknown = set(data.keys()) - _VALID_HOOK_PHASES
    if unknown:
        raise ValueError(
            f"{hooks_file}: unknown hook phase(s): {', '.join(sorted(unknown))}"
        )

    return UpdateHooks(
        update_spec=data.get("update_spec"),
        download_sources=data.get("download_sources"),
        post_update=data.get("post_update"),
    )


def _build_hook_env(
    package: str, old_version: str, new_version: str
) -> dict[str, str]:
    """Build the environment dict passed to hook commands."""
    package_dir = RPMS_DIR / package
    spec_files = list(package_dir.glob("*.spec"))
    spec_file = str(spec_files[0]) if spec_files else ""
    return {
        **os.environ,
        "UPDATE_PACKAGE": package,
        "UPDATE_OLD_VERSION": old_version,
        "UPDATE_NEW_VERSION": new_version,
        "UPDATE_SPEC_FILE": spec_file,
        "UPDATE_PACKAGE_DIR": str(package_dir),
        "UPDATE_SOURCES_FILE": str(package_dir / "sources"),
        "UPDATE_ROOT_DIR": str(ROOT_DIR),
    }


def _run_hook(
    hook_name: str, command: str, package: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a hook command via ``bash -eo pipefail``.

    Returns the completed process on success.
    Raises ``RuntimeError`` on non-zero exit.
    """
    package_dir = RPMS_DIR / package
    logger.info(
        "%s: running %s hook command:\n%s", package, hook_name, command.rstrip()
    )
    started_at = time.monotonic()
    try:
        result = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", command],
            cwd=str(package_dir),
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
    finally:
        logger.info(
            "%s: %s hook completed after %.1fs",
            package,
            hook_name,
            time.monotonic() - started_at,
        )
    if result.returncode != 0:
        details = result.stdout.strip()
        suffix = f": {details}" if details else ""
        raise RuntimeError(
            f"{package}: {hook_name} hook failed "
            f"(exit {result.returncode}){suffix}"
        )
    logger.debug(f"{package}: {hook_name} hook stdout: {result.stdout.strip()}")
    return result


def update_spec_version(package: str, new_version: str) -> list[str]:
    """
    Update the spec file for a package to a new version and download
    new source archives.

    Default behaviour (no hooks file):

    - Sets ``Version:`` to *new_version*
    - Resets ``Release:`` to ``0.1%{?dist}`` (unless ``%autorelease``
      is used).  ``0.1`` is chosen so that when the same version is
      later imported from Fedora (with ``Release >= 1``), it sorts
      higher and replaces this locally-built version.
    - Downloads new source archives and updates the ``sources`` file
    - Marks the package metadata as modified

    If ``metadata/<package>.update-hooks.yaml`` exists, the hook commands
    defined there replace (or extend) the default phases:

    - **update_spec** — replaces the Version/Release update described
      above.
    - **download_sources** — replaces the default URL-based source
      download; stdout lines are treated as filenames to upload to the
      lookaside cache.
    - **post_update** — runs after the spec and source phases (additive).

    If ``metadata/<package>.source-pipeline.yaml`` exists, it takes
    priority over both the download_sources hook and the generic default
    for the source-download phase (spec update and post_update still run
    as above): the gorget source-pipeline tool runs instead, and its
    output replaces the ``sources`` file wholesale. See
    ``_run_gorget_pipeline``. The global ``skip_pipeline`` flag
    (``--skip-pipeline``) forces the legacy path even when a
    source-pipeline definition exists.

    Returns:
        List of downloaded source filenames

    Raises:
        FileNotFoundError: If spec file is not found
        RuntimeError: If a hook command or the gorget pipeline fails
    """
    from specfile import Specfile

    package_dir = RPMS_DIR / package
    spec_files = list(package_dir.glob("*.spec"))
    if len(spec_files) != 1:
        raise FileNotFoundError(
            f"Expected exactly one .spec file in {package_dir}, "
            f"found {len(spec_files)}"
        )

    spec_file = spec_files[0]

    # Load optional per-package hooks
    hooks = _load_update_hooks(package)

    # --- Phase 1: spec update ------------------------------------------------
    if hooks and hooks.update_spec:
        # Recover old_version from metadata (the hook will mutate the
        # spec file directly, so we cannot rely on Specfile to tell us
        # the pre-update version).
        meta = get_package_metadata(package)
        old_version = meta.get("version") if meta else None
        if not old_version:
            old_version = parse_spec_version(package_dir) or new_version

        env = _build_hook_env(package, old_version, new_version)
        _run_hook("update_spec", hooks.update_spec, package, env)
    else:
        # Default specfile-library path
        logger.info("%s: loading spec with specfile", package)
        spec = Specfile(str(spec_file), sourcedir=str(package_dir))
        old_version = spec.expanded_version
        with spec.tags() as tags:
            version_tag_value = tags.version.value

        logger.info("%s: updating spec version", package)
        if version_tag_value == old_version:
            # The Version tag is already literal. Avoid scanning every macro and
            # tag for possible substitutions; large generated Provides lists can
            # contain the same version and make that search prohibitively slow.
            spec.update_tag("Version", new_version, protected_entities=".*")
        else:
            # Preserve macro indirection when Version expands from another value.
            spec.update_version(new_version)
        if not spec.has_autorelease:
            logger.info("%s: updating spec release", package)
            # Use Release 0.1 so that when the same version is later
            # imported from Fedora (with Release >= 1), it sorts higher
            # and replaces this locally-built version.
            spec.update_tag(
                "Release", "0.1%{?dist}", protected_entities=".*"
            )
        logger.info("%s: saving updated spec", package)
        spec.save()

    logger.info(f"{package}: updated spec {old_version} -> {new_version}")

    # --- Phase 2: source download --------------------------------------------
    pipeline_file = None if skip_pipeline else _load_source_pipeline(package)
    if pipeline_file:
        downloaded = _run_gorget_pipeline(
            package, old_version, new_version, pipeline_file,
        )
    elif hooks and hooks.download_sources:
        env = _build_hook_env(package, old_version, new_version)
        result = _run_hook(
            "download_sources", hooks.download_sources, package, env,
        )
        # Each non-empty stdout line is a filename to upload
        downloaded = []
        sources_path = package_dir / "sources"
        sources_entries = _parse_sources_file(sources_path)

        for line in result.stdout.splitlines():
            filename = line.strip()
            if not filename:
                continue
            filepath = package_dir / filename
            if not filepath.exists():
                raise FileNotFoundError(
                    f"{package}: download_sources hook listed "
                    f"'{filename}' but the file does not exist"
                )
            _upload_to_lookaside(filepath, package)
            new_hash = _compute_file_hash(filepath, "SHA512")

            # Replace existing entry whose filename matches the old
            # version, or update in-place if the filename is unchanged.
            old_filename = filename.replace(new_version, old_version)
            existing = next(
                (e for e in sources_entries if e["filename"] in (filename, old_filename)),
                None,
            )
            if existing:
                existing["filename"] = filename
                existing["hash"] = new_hash
            else:
                sources_entries.append(
                    {"algo": "SHA512", "filename": filename, "hash": new_hash}
                )
            downloaded.append(filename)

        if downloaded:
            _write_sources_file(sources_path, sources_entries)
    else:
        downloaded = download_new_sources(package, old_version, new_version)

    # --- Phase 3: post-update (additive) -------------------------------------
    if hooks and hooks.post_update:
        env = _build_hook_env(package, old_version, new_version)
        _run_hook("post_update", hooks.post_update, package, env)

    # --- Metadata bookkeeping ------------------------------------------------
    vr = parse_spec_version_release(package_dir)
    resolved_version = vr[0] if vr else new_version
    resolved_release = vr[1] if vr else None

    mark_package_modified(
        package,
        f"Update to upstream version {new_version}",
        version=resolved_version,
        release=resolved_release,
    )

    return downloaded


def discard_package_changes(package: str) -> None:
    """Discard uncommitted changes for one package after update failure."""
    paths = [f"rpms/{package}", f"metadata/{package}.json"]
    run_git("restore", "--staged", "--worktree", "--", *paths, cwd=ROOT_DIR)
    run_git("clean", "-fd", "--", f"rpms/{package}", cwd=ROOT_DIR)


def query_anitya(package: str, distro: str = DEFAULT_DISTRO) -> dict:
    """
    Query release-monitoring.org for package information.

    Uses the legacy API endpoint: /api/project/<distro>/<package_name>

    Returns the full API response as a dict, or raises an exception on error.
    """
    url = f"{ANITYA_API_BASE}/project/{distro}/{package}"

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "hummingbird-rpms-version-checker/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Package not found in {distro}")
        raise
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to release-monitoring.org: {e}")


def query_anitya_by_project_id(project_id: int) -> dict:
    """
    Query release-monitoring.org for version information by project ID.

    Uses the v2 API endpoint: /api/v2/versions/?project_id=<id>

    Returns a dict with 'stable_versions' and 'latest_version' fields,
    normalized to match the format expected by check_package_version()
    (i.e., 'version' key maps to 'latest_version').
    """
    url = f"{ANITYA_API_BASE}/v2/versions/?project_id={project_id}"

    req = urllib.request.Request(
        url, headers={"User-Agent": "hummingbird-rpms-version-checker/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    # Normalize: the v2 versions endpoint uses 'latest_version' where the
    # distro API uses 'version'.  Map it so callers can use the same key.
    return {
        "stable_versions": data.get("stable_versions", []),
        "version": data.get("latest_version"),
    }


def _matches_track_version(version: str, track_version: str) -> bool:
    """Check if a version matches a track_version prefix.

    Returns True if version equals track_version or starts with track_version + '.'.
    For example, track_version='1.26' matches '1.26', '1.26.0', '1.26.3'
    but not '1.27.0' or '1.3'.
    """
    return version == track_version or version.startswith(track_version + ".")


def check_package_version(
    package: str, distro: str = DEFAULT_DISTRO
) -> VersionCheckResult:
    """
    Check if a newer version is available for a package.

    If the metadata contains a ``release_monitoring_project_id`` field, it is
    used to identify the upstream project on release-monitoring.org (int for
    project ID, str for name).  If ``track_upstream`` is a version prefix,
    only upstream versions matching that prefix are considered.

    Args:
        package: Package name
        distro: Distribution name (default: Fedora)

    Returns:
        VersionCheckResult with comparison details
    """
    package_dir = RPMS_DIR / package

    # Get current version from spec file or metadata
    current_version = None
    if package_dir.exists():
        current_version = parse_spec_version(package_dir)

    if not current_version:
        current_version = get_version_from_metadata(package)

    if not current_version:
        return VersionCheckResult(
            package=package,
            current_version="unknown",
            upstream_version=None,
            has_update=False,
            error="Could not determine current version",
        )

    # Use metadata for Anitya lookup if available
    meta = get_package_metadata(package)
    track_version = None
    project_id = None
    version_suffix_strip = None
    if meta:
        track_upstream = meta.get("track_upstream")
        if track_upstream and track_upstream != "latest":
            track_version = track_upstream
        project_id = meta.get("release_monitoring_project_id")
        version_suffix_strip = meta.get("version_suffix_strip")

    # Query release-monitoring.org
    try:
        if isinstance(project_id, int):
            anitya_data = query_anitya_by_project_id(project_id)
        else:
            lookup_name = project_id if isinstance(project_id, str) else package
            anitya_data = query_anitya(lookup_name, distro)
    except (ValueError, ConnectionError, urllib.error.URLError) as e:
        return VersionCheckResult(
            package=package,
            current_version=current_version,
            upstream_version=None,
            has_update=False,
            error=str(e),
        )

    # Strip a known suffix from upstream versions if configured
    # (e.g., swift-lang reports "6.3.3-RELEASE" from Anitya).
    if version_suffix_strip:
        def _strip_suffix(v: str) -> str:
            return v.removesuffix(version_suffix_strip) if v else v

        if anitya_data.get("stable_versions"):
            anitya_data["stable_versions"] = [
                _strip_suffix(v) for v in anitya_data["stable_versions"]
            ]
        if anitya_data.get("version"):
            anitya_data["version"] = _strip_suffix(anitya_data["version"])

    # Prefer stable_versions[0] over version field, as version can sometimes
    # contain incorrect data (e.g., development tags that aren't real releases)
    stable_versions = anitya_data.get("stable_versions", [])

    # If track_version is set, filter stable_versions to matching prefix
    if track_version and stable_versions:
        filtered = [v for v in stable_versions if _matches_track_version(v, track_version)]
        if filtered:
            stable_versions = filtered
        else:
            # No matching versions found in stable_versions
            logger.debug(
                "%s: no stable versions matching track_version %s",
                package, track_version,
            )
            stable_versions = []

    if stable_versions:
        upstream_version = stable_versions[0]
    else:
        upstream_version = anitya_data.get("version")
        # Apply track_version filter to fallback version field too
        if track_version and upstream_version:
            if not _matches_track_version(upstream_version, track_version):
                upstream_version = None

    if not upstream_version:
        return VersionCheckResult(
            package=package,
            current_version=current_version,
            upstream_version=None,
            has_update=False,
            error="No upstream version reported by Anitya",
            anitya_project_id=anitya_data.get("id"),
        )

    # Compare versions
    comparison = compare_versions(current_version, upstream_version)
    has_update = comparison > 0

    return VersionCheckResult(
        package=package,
        current_version=current_version,
        upstream_version=upstream_version,
        has_update=has_update,
        anitya_project_id=anitya_data.get("id"),
    )


def get_all_packages() -> list[str]:
    """Get list of all packages in the rpms directory."""
    packages = []
    for item in RPMS_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            # Check if it has a spec file
            if list(item.glob("*.spec")):
                packages.append(item.name)
    return sorted(packages)


def list_all(args: argparse.Namespace) -> None:
    """
    List ALL packages and display a single sorted table.

    This ignores the track_upstream metadata filter and checks every
    package in the rpms directory.
    """
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if args.quiet:
        logger.setLevel(logging.WARNING)

    packages = get_all_packages()
    if not args.quiet and not args.json:
        logger.info(f"Checking {len(packages)} packages...")

    entries: list[dict[str, str]] = []

    for i, package in enumerate(packages):
        if not args.quiet and not args.json:
            print(
                f"\rChecking {i + 1}/{len(packages)}: {package:<40}",
                end="",
                flush=True,
            )

        result = check_package_version(package, args.distro)

        # Determine tracking status from metadata
        meta = get_package_metadata(package)
        tracking = "yes" if (meta and meta.get("track_upstream")) else "no"

        # Determine status and upstream version display
        if result.error:
            if "not found" in result.error.lower():
                status = "not found in release-monitoring.org"
            else:
                status = result.error
            upstream_display = "-"
        elif result.has_update:
            status = "update available"
            upstream_display = result.upstream_version or "-"
        else:
            status = "up-to-date"
            upstream_display = result.upstream_version or "-"

        entries.append(
            {
                "package": package,
                "current_version": result.current_version,
                "upstream_version": upstream_display,
                "tracking": tracking,
                "status": status,
            }
        )

        # Rate limiting
        if i < len(packages) - 1:
            time.sleep(args.delay)

    # Clear progress line
    if not args.quiet and not args.json:
        print("\r" + " " * 60 + "\r", end="")

    # Sort alphabetically by package name
    entries.sort(key=lambda e: e["package"])

    if args.json:
        print(json.dumps(entries, indent=2))
    else:
        # Table output
        header = (
            f"{'Package':<30} {'Current':<15} {'Upstream':<15}"
            f" {'Tracking':<10} {'Status'}"
        )
        print()
        print(header)
        print("-" * len(header))
        for e in entries:
            print(
                f"{e['package']:<30} {e['current_version']:<15}"
                f" {e['upstream_version']:<15} {e['tracking']:<10}"
                f" {e['status']}"
            )
        print()
        print(f"Total: {len(entries)} packages")

    sys.exit(0)



def run_check(args: argparse.Namespace) -> None:
    """
    Check tracked packages for upstream version updates.

    This is the core logic shared by the 'check' subcommand and the
    legacy (no subcommand) invocation.
    """
    global sign_off, skip_pipeline
    sign_off = getattr(args, "sign_off", False)
    skip_pipeline = getattr(args, "skip_pipeline", False)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if args.quiet:
        logger.setLevel(logging.WARNING)

    # Determine which packages to check
    if args.packages:
        packages = args.packages
    else:
        packages = get_all_packages()
        # Filter to only packages with track_upstream set in metadata
        tracked = []
        for pkg in packages:
            meta = get_package_metadata(pkg)
            if meta and meta.get("track_upstream"):
                tracked.append(pkg)
        packages = tracked
        if not args.quiet:
            logger.info(f"Checking {len(packages)} packages...")

    results: list[VersionCheckResult] = []
    updates_found = 0
    errors_found = 0

    for i, package in enumerate(packages):
        if not args.quiet and not args.json:
            # Progress indicator
            print(
                f"\rChecking {i + 1}/{len(packages)}: {package:<40}", end="", flush=True
            )

        result = check_package_version(package, args.distro)
        results.append(result)

        if result.has_update:
            updates_found += 1
        if result.error:
            errors_found += 1

        # Rate limiting
        if i < len(packages) - 1:
            time.sleep(args.delay)

    # Clear progress line
    if not args.quiet and not args.json:
        print("\r" + " " * 60 + "\r", end="")

    # Update spec files if requested
    updates_applied = 0
    if args.update:
        for result in results:
            if not result.has_update or not result.upstream_version:
                continue
            logger.info(
                "%s: applying upstream update %s -> %s",
                result.package,
                result.current_version,
                result.upstream_version,
            )
            try:
                downloaded = update_spec_version(
                    result.package, result.upstream_version
                )
                result.downloaded_sources = downloaded

                # Add downloaded sources to .gitignore so they
                # are not committed (they live in the lookaside cache)
                if downloaded:
                    _update_gitignore(RPMS_DIR / result.package, downloaded)

                # Commit the changes (no -f so .gitignore is respected)
                run_git(
                    "add",
                    f"rpms/{result.package}",
                    f"metadata/{result.package}.json",
                    cwd=ROOT_DIR,
                )
                commit_msg = (
                    f"Update {result.package} to"
                    f" {result.upstream_version}\n\n"
                    f"Upstream version detected via"
                    f" release-monitoring.org"
                )
                run_git_commit("-m", commit_msg, cwd=ROOT_DIR)
            except (
                OSError,
                RuntimeError,
                ValueError,
                subprocess.CalledProcessError,
                urllib.error.URLError,
            ) as e:
                result.update_error = str(e)
                logger.error("%s: failed to apply update: %s", result.package, e)
                try:
                    discard_package_changes(result.package)
                except subprocess.CalledProcessError as cleanup_error:
                    cleanup_msg = (
                        f"{result.update_error}; failed to clean working tree: "
                        f"{cleanup_error}"
                    )
                    result.update_error = cleanup_msg
                    logger.error("%s: %s", result.package, cleanup_msg)
                    raise
                continue

            result.updated = True
            updates_applied += 1

    # Output results
    if args.json:
        output = {
            "total_packages": len(packages),
            "updates_available": updates_found,
            "updates_applied": updates_applied,
            "errors": errors_found,
            "results": [
                {
                    "package": r.package,
                    "current_version": r.current_version,
                    "upstream_version": r.upstream_version,
                    "has_update": r.has_update,
                    "updated": r.updated,
                    "downloaded_sources": r.downloaded_sources,
                    "error": r.error,
                    "update_error": r.update_error,
                    "anitya_project_id": r.anitya_project_id,
                }
                for r in results
                if args.all or r.has_update or r.error
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        # Text output
        if updates_found > 0:
            if args.update:
                print("\nPackages updated:")
                print("-" * 80)
                print(
                    f"{'Package':<30} {'Current':<15} {'Upstream':<15} {'Status':<10}"
                )
                print("-" * 80)
            else:
                print("\nPackages with available updates:")
                print("-" * 70)
                print(f"{'Package':<30} {'Current':<15} {'Upstream':<15}")
                print("-" * 70)

            for r in results:
                if r.has_update:
                    if args.update:
                        status = "UPDATED" if r.updated else "FAILED"
                        print(
                            f"{r.package:<30} {r.current_version:<15} {r.upstream_version:<15} {status}"
                        )
                        if r.downloaded_sources:
                            for src in r.downloaded_sources:
                                print(f"  -> {src}")
                        if r.update_error:
                            print(f"  Error: {r.update_error}")
                    else:
                        print(
                            f"{r.package:<30} {r.current_version:<15} {r.upstream_version:<15}"
                        )

        if args.all:
            # Show up-to-date packages
            up_to_date = [r for r in results if not r.has_update and not r.error]
            if up_to_date:
                print("\nUp-to-date packages:")
                print("-" * 70)
                for r in up_to_date:
                    print(f"{r.package:<30} {r.current_version:<15}")

        if errors_found > 0:
            print("\nPackages with errors:")
            print("-" * 70)
            for r in results:
                if r.error:
                    print(f"{r.package:<30} {r.error}")

        # Summary
        print()
        if args.update:
            update_failures = updates_found - updates_applied
            print(
                f"Summary: {updates_applied} specs updated, "
                f"{update_failures} update failures, "
                f"{len(results) - updates_found - errors_found} up-to-date, "
                f"{errors_found} errors"
            )
        else:
            print(
                f"Summary: {updates_found} updates available, "
                f"{len(results) - updates_found - errors_found} up-to-date, "
                f"{errors_found} errors"
            )

    # Exit code: 0 if no updates, 1 if updates found, 2 if errors
    if errors_found > 0 and updates_found == 0:
        sys.exit(2)
    elif updates_found > 0:
        sys.exit(1)
    else:
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Check for upstream version updates using release-monitoring.org",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s check              Check tracked packages, show only updates
  %(prog)s check curl openssl Check specific packages
  %(prog)s check --all        Show all packages including up-to-date
  %(prog)s check --json       Output results as JSON
  %(prog)s check --update curl  Update curl spec to new upstream version
  %(prog)s list               List all packages with status table
  %(prog)s list --json        List all packages as JSON
        """,
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    subparsers.required = True

    # 'check' subcommand
    check_parser = subparsers.add_parser(
        "check",
        help="Check tracked packages for upstream updates",
        description=(
            "Check tracked packages for upstream version updates. "
            "When package names are given, checks those specific packages. "
            "Otherwise checks all packages with track_upstream set."
        ),
    )
    check_parser.add_argument(
        "packages", nargs="*",
        help="Package names to check (default: all tracked)",
    )
    check_parser.add_argument(
        "--all", "-a", action="store_true",
        help="Show all packages, not just those with updates",
    )
    check_parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output results as JSON",
    )
    check_parser.add_argument(
        "--update", "-u", action="store_true",
        help="Update spec files to the new upstream version",
    )
    check_parser.add_argument(
        "--distro", "-d", default=DEFAULT_DISTRO,
        help=f"Distribution to look up in Anitya (default: {DEFAULT_DISTRO})",
    )
    check_parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output",
    )
    check_parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress progress output (implies not --verbose)",
    )
    check_parser.add_argument(
        "--delay", type=float, default=API_DELAY,
        help=f"Delay between API requests in seconds (default: {API_DELAY})",
    )
    check_parser.add_argument(
        "-s", "--sign-off", action="store_true",
        help="Add Signed-off-by trailer to commit messages",
    )
    check_parser.add_argument(
        "--skip-pipeline", action="store_true",
        help=(
            "Force the legacy hook/default source-download path even when "
            "metadata/<package>.source-pipeline.yaml exists"
        ),
    )

    # 'list' subcommand
    list_parser = subparsers.add_parser(
        "list",
        help="List ALL packages with upstream version status",
        description=(
            "List all packages with upstream version status in a single "
            "table, regardless of tracking configuration."
        ),
    )
    list_parser.add_argument(
        "--distro", "-d", default=DEFAULT_DISTRO,
        help=f"Distribution to look up in Anitya (default: {DEFAULT_DISTRO})",
    )
    list_parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose output",
    )
    list_parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress progress output",
    )
    list_parser.add_argument(
        "--delay", type=float, default=API_DELAY,
        help=f"Delay between API requests in seconds (default: {API_DELAY})",
    )
    list_parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    if args.subcommand == "check":
        run_check(args)
    elif args.subcommand == "list":
        list_all(args)


if __name__ == "__main__":
    main()
