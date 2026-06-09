#!/usr/bin/env python3
"""
dist-git importer for Hummingbird rpms repository.

This tool imports and syncs Fedora/CentOS dist-git packages
into the local rpms/ directory while tracking metadata in per-package
import.json files.
"""

import argparse
import http.client
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import xmlrpc.client
import yaml
from packaging.version import InvalidVersion, Version
from pathlib import Path
from specfile import Specfile
from typing import Iterator, Literal, NotRequired, TypedDict, cast

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / 'rpms'
METADATA_DIR = ROOT_DIR / 'metadata'
RELEASES_JSON = ROOT_DIR / 'upstream-releases.json'
PACKAGE_OVERRIDES_YAML = ROOT_DIR / 'ci' / 'package-overrides.yaml'
RENAMED_PACKAGES_JSON = ROOT_DIR / 'ci' / 'renamed_packages.json'
UPDATE_STATE_FILE = ROOT_DIR / '.dist_git_update_state.json'

# Match %autorelease in spec files (possibly with braces/options like -b, -e, etc.)
# Can be anywhere, not just in the Release: line, as some packages like nodejs* use
# it through indirect macros.  Nothing after %autorelease ever needs to be preserved,
# so we consume everything to end of line.
AUTORELEASE_PATTERN = r'%\{?\??autorelease.*'

# Match dist-git sources file format: ALGO (filename) = hash
SOURCES_LINE_PATTERN = re.compile(r'^(\w+)\s+\((.+?)\)\s+=\s+(\w+)$')

# When the LATEST version of a virtual BuildRequires changes, rebuild all its dependents
# See get_virtual_buildrequires_translation() below for details
VIRTUAL_BUILDREQUIRES_PATTERNS = {
    r'^golang1\.(.+)$': 'go-rpm-macros',
    r'^golang-fips1\.(.+)$': 'go-rpm-macros',
    r'^python3\.(.+)$': 'python3-devel',
}

# Global imports dict, loaded at startup
imports: dict[str, 'PackageMetadata'] = {}

# Global releases dict, loaded at startup
releases: dict[str, dict[str, str]] = {}

# Global sign-off flag, set from command line
sign_off: bool = False


class PackageMetadata(TypedDict):
    """Metadata stored in metadata/<package>.json for each package."""
    source: NotRequired[str]  # Not present for native packages
    branch: NotRequired[str]  # Not present for native packages
    sha: NotRequired[str]  # Not present for native packages
    version: str
    release: str
    modification_status: NotRequired[Literal["clean", "modified", "native"]]
    modification_reason: NotRequired[str]  # Only for 'modified'
    track_upstream: NotRequired[str]  # "latest" or version prefix (e.g., "1.26")
    release_monitoring_project_id: NotRequired[int | str]  # Anitya project ID (int) or upstream name (str)
    upstream_repo: NotRequired[str]  # Canonical upstream git repository URL
    upstream_branch: NotRequired[str]  # Upstream branch (versioned packages sharing a repo)
    cve_product: NotRequired[str]  # CVE vendor/product override (e.g., "F5 / NGINX Open Source")
    version_transform: NotRequired[str]  # Version transform rule (e.g., "dotnet_sdk_to_runtime")


class KojiBuild(TypedDict, total=False):
    """Koji build result from getBuild() API."""
    build_id: int
    nvr: str
    state: int
    source: str


def uses_autorelease(package_dir: Path) -> bool:
    """Check if the spec file in package_dir uses %autorelease."""
    spec_files = list(package_dir.glob('*.spec'))
    if len(spec_files) != 1:
        return False

    spec_content = spec_files[0].read_text()
    return bool(re.search(AUTORELEASE_PATTERN, spec_content))


def query_autorelease_from_mdapi(package_name: str, branch: str, fallback_release: str) -> str | None:
    """Query MDAPI for autorelease value.

    Returns the resolved release number (without dist suffix), or None if query fails.
    """
    logging.info("Upstream uses %%autorelease, querying MDAPI for %s (%s)...", package_name, branch)
    mdapi_build = get_mdapi_latest_build(package_name, branch)

    if mdapi_build:
        release = mdapi_build['release']
        # Strip dist suffix (e.g., "1.fc42" -> "1")
        release = re.sub(r'\.(fc|el)\d+$', '', release)
        logging.info("MDAPI latest release: %s", release)
        return release
    else:
        logging.warning("No MDAPI build found for %s, keeping %%autorelease", package_name)
        return None


def replace_autorelease_in_spec(package_dir: Path, release: str) -> None:
    """Replace %autorelease in spec file with actual release value."""
    spec_files = list(package_dir.glob('*.spec'))
    if spec_files:
        spec_file = spec_files[0]
        spec_content = spec_file.read_text()
        new_content = re.sub(
            AUTORELEASE_PATTERN,
            f'{release}%{{?dist}}',
            spec_content
        )
        if new_content == spec_content:
            raise ValueError(f"Failed to replace %autorelease in {spec_file.name}")
        spec_file.write_text(new_content)
        logging.info("Replaced %%autorelease with %s%%{?dist} in %s", release, spec_file.name)


def parse_spec_version(package_dir: Path) -> tuple[str, str]:
    """Extract version and release from package directory's spec file."""
    # Find the spec file
    spec_files = list(package_dir.glob('*.spec'))
    if len(spec_files) != 1:
        sys.exit(f"ERROR: Expected exactly one .spec file in {package_dir}, found {spec_files}")

    spec_file = spec_files[0]

    # Use rpmspec to query the resolved version and release
    # Set dist to %{nil} to get release without dist suffix
    # Set _sourcedir so rpmspec can find source files referenced in the spec
    rpmspec = subprocess.run(
        ['rpmspec', '-q', '--qf', '%{VERSION}\n%{RELEASE}\n',
         '--define=dist %{nil}', f'--define=_sourcedir {package_dir}', '--srpm', str(spec_file)],
        stdout=subprocess.PIPE, text=True, check=True)
    lines = rpmspec.stdout.strip().splitlines()
    try:
        return lines[0], lines[1]
    except IndexError as e:
        raise ValueError(f"Unexpected rpmspec output for {spec_file}: {rpmspec.stdout}") from e


# Pre-release detection patterns (compiled at module level for performance)
PRERELEASE_PATTERNS = [
    # Tilde notation (RPM-style: 5.3.0~rc1)
    (re.compile(r'~(rc|alpha|beta|pre|dev|snapshot|git)\d*', re.IGNORECASE),
     "tilde pre-release marker"),

    # Hyphen/dot with pre-release suffix (use word boundaries to avoid false matches)
    (re.compile(r'[-.](?:rc|alpha|beta|pre|dev|snapshot)\d*\b', re.IGNORECASE),
     "pre-release suffix"),

    # Git/snapshot timestamps (git20240101, snapshot20240101)
    (re.compile(r'\b(git|snapshot)\d{6,}', re.IGNORECASE),
     "development snapshot"),

    # Standalone development markers at end (1.0dev, 2.0pre)
    (re.compile(r'(dev|pre)\b', re.IGNORECASE),
     "development marker"),
]


def is_prerelease(version: str, release: str | None = None) -> tuple[bool, str | None]:
    """Detect if a version or release string contains pre-release markers.

    Args:
        version: Version string to check (e.g., "5.3.0~rc1", "2.0-beta1")
        release: Optional release string to check (e.g., "0.rc1.15", "59")

    Returns:
        (is_prerelease, pattern_matched): Tuple of boolean and optional pattern description

    Examples:
        >>> is_prerelease("5.3.0~rc1")
        (True, "tilde pre-release marker (~rc1)")
        >>> is_prerelease("2.0.3")
        (False, None)
        >>> is_prerelease("7.0.0", "0.rc1.15")
        (True, "pre-release suffix (.rc1) in release")
    """
    # Check version first
    for pattern, description in PRERELEASE_PATTERNS:
        match = pattern.search(version)
        if match:
            return (True, f"{description} ({match.group(0)})")

    # Check release if provided
    if release:
        for pattern, description in PRERELEASE_PATTERNS:
            match = pattern.search(release)
            if match:
                return (True, f"{description} ({match.group(0)}) in release")

    return (False, None)


def rename_spec_validate(original_name: str, original_dir: Path) -> str:
    """Read the new package name from spec file and validate it has been changed.

    Returns the new package name from the spec file.
    Exits with error if the Name field has not been changed from the original.
    """

    spec_file = original_dir / f"{original_name}.spec"
    current_spec = Specfile(str(spec_file), sourcedir=original_dir)
    new_name = current_spec.name

    spec_file_git_path = f'rpms/{original_name}/{original_name}.spec'

    git_result = run_git('show', f'HEAD:{spec_file_git_path}', cwd=ROOT_DIR, check=False)
    if git_result.returncode != 0:
        sys.exit(f"ERROR: Could not retrieve original spec file from git: {spec_file_git_path}")

    # Use a temp directory so we can copy source files needed by %load directives
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_spec = Path(tmpdir) / f"{original_name}.spec"
        tmp_spec.write_text(git_result.stdout)

        # Copy all non-spec source files to temp dir (for %load macros.* etc.)
        for src_file in original_dir.iterdir():
            if src_file.is_file() and src_file.suffix != '.spec':
                shutil.copy(src_file, tmpdir)

        original_spec = Specfile(str(tmp_spec), sourcedir=Path(tmpdir))
        original_spec_name = original_spec.name

    if original_spec_name == new_name:
        sys.exit(f"ERROR: Name field in {spec_file.name} has not been changed (still '{new_name}')\n"
                 f"       Please edit the spec file and change the Name: field before running rename")
    return new_name


def load_package_metadata(package_name: str) -> PackageMetadata | None:
    """Load package metadata from metadata/<package>.json."""
    import_file = METADATA_DIR / f'{package_name}.json'
    if not import_file.exists():
        return None
    with open(import_file) as f:
        data = json.load(f)
        if not data:
            return None
        return cast(PackageMetadata, data)


def save_package_metadata(package_name: str, metadata: PackageMetadata) -> None:
    """Save package metadata to metadata/<package>.json."""
    import_file = METADATA_DIR / f'{package_name}.json'
    METADATA_DIR.mkdir(exist_ok=True)
    with open(import_file, 'w') as f:
        json.dump(metadata, f, indent=2, sort_keys=True)
        f.write('\n')  # Ensure trailing newline


def get_all_imported_packages() -> dict[str, PackageMetadata]:
    """Scan metadata/ directory and load all package metadata files."""
    all_imports: dict[str, PackageMetadata] = {}
    if not METADATA_DIR.exists():
        return all_imports

    for metadata_file in METADATA_DIR.glob('*.json'):
        package_name = metadata_file.stem
        metadata = load_package_metadata(package_name)
        if metadata:
            all_imports[package_name] = metadata

    return all_imports


def _parse_sources_file(sources_path: Path) -> Iterator[str]:
    """
    Parse a dist-git 'sources' file.

    Each line has format: ALGO (filename) = hash

    Yields filenames.
    """
    for line in sources_path.read_text().splitlines():
        match = SOURCES_LINE_PATTERN.match(line.strip())
        if match:
            yield match.group(2)  # filename


def list_archive_contents(filepath: Path) -> None:
    """List contents of a tar or zip archive."""
    name_lower = filepath.name.lower()

    # Skip signature files and other non-archives
    if name_lower.endswith(('.sig', '.asc', '.sign')):
        return

    # Determine archive type and command
    if name_lower.endswith(('.tar.gz', '.tar.bz2', '.tar.xz', '.tar.zst', '.tgz', '.tar')):
        cmd = ['tar', 'tf', str(filepath)]
    elif name_lower.endswith('.zip'):
        cmd = ['unzip', '-l', str(filepath)]
    else:
        raise RuntimeError(f"Unknown archive type: {filepath.name}")

    subprocess.run(cmd, check=True)


def ls_sources(package: str) -> None:
    """List 'sources' archive contents for a package."""
    # Determine source URL from metadata
    metadata = imports[package]

    package_dir = RPMS_DIR / package
    sources_path = package_dir / 'sources'
    if not sources_path.exists():
        logging.info(f"No {sources_path} file")
        sys.exit(0)

    try:
        source_url = metadata['source']
    except KeyError:
        # Native packages don't have a source URL - use Hummingbird lookaside
        if metadata.get('modification_status') != 'native':
            logging.error(f"Package {package} has no source URL and is not marked as native")
            sys.exit(1)
        source_url = f'https://gitlab.com/redhat/hummingbird/rpms/{package}.git'

    # Download source archives
    subprocess.run(
        ['dist-git-client', '--configdir', str(ROOT_DIR / 'mock'),
         '--loglevel', 'warning',
         '--forked-from', source_url, 'sources'],
        cwd=str(package_dir), check=True)

    # List contents of each source file
    for filename in _parse_sources_file(sources_path):
        print(f"==== {filename} ====")
        list_archive_contents(package_dir / filename)


def run_git(*args: str, cwd: Path | str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git command and return its stdout/exit code.

    For network operations (clone, fetch, pull), retries up to 3 times with exponential backoff
    to handle transient network failures.
    """
    # Network operations that should be retried on failure
    network_ops = {'clone', 'fetch', 'pull'}
    should_retry = len(args) > 0 and args[0] in network_ops

    max_retries = 3 if should_retry else 1
    retry_delay = 2  # Initial delay in seconds

    for attempt in range(max_retries):
        try:
            return subprocess.run(['git', *args], cwd=cwd, check=check, stdout=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as e:
            if attempt < max_retries - 1:
                logging.warning("Git %s failed (attempt %d/%d): %s", args[0], attempt + 1, max_retries, e)
                logging.info("Retrying in %d seconds...", retry_delay)
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                # Last attempt failed, re-raise the exception
                raise

    # This should never be reached since max_retries >= 1 and loop always returns or raises
    raise RuntimeError("Unexpected: git command loop completed without return or exception")


def run_git_commit(*args: str, cwd: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    """Run git commit with optional --signoff flag."""
    commit_args = ['commit']
    if sign_off:
        commit_args.append('--signoff')
    commit_args.extend(args)
    return run_git(*commit_args, cwd=cwd)


def check_jinja2_available() -> None:
    """Check if jinja2 command-line tool is available."""
    try:
        subprocess.run(['jinja2', '--version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("ERROR: jinja2 command-line tool is not available.\n"
                 "       Please install jinja2-cli (pip install jinja2-cli) or similar package.")


def expand_url_shortcut(url: str) -> str:
    """Expand URL shortcuts to full dist-git URLs.

    Supported shortcuts:
        fedora/<package>  -> https://src.fedoraproject.org/rpms/<package>.git
        centos/<package>  -> https://gitlab.com/redhat/centos-stream/rpms/<package>.git
    """
    if url.startswith('fedora/'):
        package = url.removeprefix('fedora/')
        return f'https://src.fedoraproject.org/rpms/{package}.git'
    if url.startswith('centos/'):
        package = url.removeprefix('centos/')
        return f'https://gitlab.com/redhat/centos-stream/rpms/{package}.git'
    return url


def update_releases() -> None:
    """Update upstream-releases.json from Bodhi API and known CentOS Stream versions.

    Fetches active Fedora releases from Bodhi. The 'rawhide' branch is automatically
    excluded from the JSON file since it's resolved at runtime to the highest numbered
    Fedora release (e.g., if f43 and f44 exist, rawhide resolves to f44).
    """

    releases: dict[str, dict[str, str]] = {}

    # Fedora: get them from Bodhi
    logging.info("Fetching Fedora releases from Bodhi API...")
    url = 'https://bodhi.fedoraproject.org/releases/?exclude_archived=true'
    releases["fedora"] = {}

    # use curl for robustness
    output = subprocess.check_output(
        ['curl', '--silent', '--show-error', '--fail', '--retry', '5', '--max-time', '10', url],
        text=True
    )

    data = json.loads(output)
    fedora_releases_list = [r for r in data['releases'] if r['id_prefix'] == 'FEDORA']
    for release in fedora_releases_list:
        if release['branch'] == 'rawhide':
            # Don't add rawhide as a branch key, but DO add its dist_tag (e.g., f45)
            # This handles the case where a new Fedora version hasn't branched yet
            # and only exists as rawhide in Bodhi
            releases['fedora'][release['dist_tag']] = release['dist_tag']
        else:
            # Add normal branches (f43, f44, etc.)
            releases['fedora'][release['branch']] = release['dist_tag']

    # Log what rawhide will resolve to (for informational purposes)
    numbered_releases = []
    for dist_tag in releases['fedora'].values():
        if match := re.match(r'^f(\d+)$', dist_tag):
            numbered_releases.append(int(match.group(1)))
    if numbered_releases:
        highest = f'f{max(numbered_releases)}'
        logging.info(f"Rawhide will auto-resolve to {highest} (highest Fedora release)")

    # CentOS Stream: add known active streams
    # These don't have a dynamic API, so we hardcode active versions
    logging.info("Adding CentOS Stream releases...")
    releases['centos'] = {
        'c9s': 'el9',
        'c10s': 'el10',
    }

    with open(RELEASES_JSON, 'w') as f:
        json.dump(releases, f, indent=2, sort_keys=True)
        f.write('\n')

    logging.info("Updated upstream-releases.json")


def update_package_overrides(original_name: str, new_name: str) -> None:
    """Update package name in package-overrides.yaml."""
    with open(PACKAGE_OVERRIDES_YAML, 'r') as f:
        overrides = yaml.safe_load(f) or {}
    if original_name in overrides:
        # Rename the key while preserving order
        overrides[new_name] = overrides.pop(original_name)
        with open(PACKAGE_OVERRIDES_YAML, 'w') as f:
            yaml.dump(overrides, f, default_flow_style=False, sort_keys=False)
        logging.info(f"Renamed {original_name} to {new_name} in {PACKAGE_OVERRIDES_YAML}")


def update_rename_record(old: str, new: str) -> None:
    """Record a package rename in renamed_packages.json."""
    # Load existing rename records
    renames = {}
    if RENAMED_PACKAGES_JSON.exists():
        with open(RENAMED_PACKAGES_JSON, 'r') as f:
            renames = json.load(f)

        if new in renames:
            logging.info(f"Package '{new}' already exists in rename records (was renamed from '{renames[new]}')")
            return

    renames[new] = old

    with open(RENAMED_PACKAGES_JSON, 'w') as f:
        json.dump(renames, f, indent=2, sort_keys=True)
        f.write('\n')  # Ensure trailing newline

    logging.info(f"Recorded rename: {old} -> {new} in {RENAMED_PACKAGES_JSON}")


def rename(original_name: str) -> None:
    """Rename a package for RPM versioning (i.e. tomcat -> tomcat11"""

    original_dir = RPMS_DIR / original_name

    if not original_dir.exists():
        sys.exit(f"ERROR: Package directory {original_dir} does not exist")

    new_name = rename_spec_validate(original_name, original_dir)
    logging.info("Renaming package '%s' to '%s' in '%s'", original_name, new_name, RPMS_DIR)

    new_dir = RPMS_DIR / new_name
    original_metadata_file = METADATA_DIR / f'{original_name}.json'
    new_metadata_file = METADATA_DIR / f'{new_name}.json'

    if new_dir.exists():
        sys.exit(f"ERROR: Package directory {new_dir} already exists")

    if not original_metadata_file.exists():
        sys.exit(f"ERROR: Package metadata file {original_metadata_file} does not exist")

    if new_metadata_file.exists():
        sys.exit(f"ERROR: Package metadata file {new_metadata_file} already exists")


    run_git('mv', f'rpms/{original_name}', f'rpms/{new_name}', cwd=ROOT_DIR)
    logging.info(f"Successfully renamed {original_dir} to {new_dir}")
    run_git('mv', f'metadata/{original_name}.json', f'metadata/{new_name}.json', cwd=ROOT_DIR)
    logging.info(f"Successfully renamed {original_metadata_file} to {new_metadata_file}")

    update_package_overrides(original_name, new_name)
    update_rename_record(original_name, new_name)

    logging.info("Running make generate to update Tekton resources...")
    subprocess.run(['make', 'generate'], cwd=ROOT_DIR, check=True)

    # Commit the changes
    logging.info("Committing changes for rename.")
    run_git('add', '-f',
            str(PACKAGE_OVERRIDES_YAML.relative_to(ROOT_DIR)),
            str(RENAMED_PACKAGES_JSON.relative_to(ROOT_DIR)),
            'konflux-templates', '.tekton', cwd=ROOT_DIR)
    commit_msg = f"Rename {original_name} to {new_name}"
    run_git_commit('-m', commit_msg, cwd=ROOT_DIR)

    logging.info("Renaming complete")


def get_dist_tag(branch: str) -> str:
    """Get dist_tag for a branch from upstream-releases.json.

    For Fedora branches, looks up in upstream-releases.json.
    For rawhide, automatically returns the highest numbered Fedora release.
    For CentOS Stream, uses direct conversion.
    """
    # Special case: rawhide always maps to the highest Fedora release
    if branch == 'rawhide':
        highest = get_highest_fedora_release()
        if highest:
            return highest
        # Fall through to regular lookup if no numbered releases found

    # Check Fedora releases first
    if branch in releases.get('fedora', {}):
        return releases['fedora'][branch]

    # CentOS Stream: c9s -> el9, c10s -> el10
    if match := re.match(r'^c(\d+)s$', branch):
        return f'el{match.group(1)}'

    sys.exit(f"ERROR: Unknown branch '{branch}'. Run 'update-releases' to refresh.")



def _is_forward_branch_move(branch_changed: bool, old_branch: str, new_branch: str) -> bool:
    """Check if a branch change should allow version downgrades.

    Returns True for:
    - Forward numbered moves (e.g., f43 -> f44)
    - Moving from rawhide to any numbered branch (pinning to a release)
    Returns False for backward numbered moves (f44 -> f43) or cross-distro moves.
    """
    if not branch_changed:
        return False

    # Moving from rawhide to a numbered branch is always allowed
    if old_branch == 'rawhide':
        return True

    old_tag = get_dist_tag(old_branch)
    new_tag = get_dist_tag(new_branch)

    old_match = re.match(r'^f(\d+)$', old_tag)
    new_match = re.match(r'^f(\d+)$', new_tag)

    if old_match and new_match:
        return int(new_match.group(1)) > int(old_match.group(1))

    return False


def get_highest_fedora_release() -> str | None:
    """Get the dist_tag for the highest numbered Fedora release.

    Scans all Fedora releases and returns the highest fNN dist_tag.
    Returns None if no numbered releases found.
    Used to automatically resolve 'rawhide' to the current development version.
    """
    fedora_releases = releases.get('fedora', {})
    numbered_releases = []
    for release_dist_tag in fedora_releases.values():
        if match := re.match(r'^f(\d+)$', release_dist_tag):
            numbered_releases.append(int(match.group(1)))

    if not numbered_releases:
        return None

    return f'f{max(numbered_releases)}'


class KojiTransport(xmlrpc.client.SafeTransport):
    """Custom XML-RPC transport with proxy support and bot detection bypass."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if proxy_url := os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy'):
            parsed = urllib.parse.urlparse(proxy_url)
            self.proxy_host = parsed.hostname
            self.proxy_port = parsed.port or 3128
        else:
            self.proxy_host = None
            self.proxy_port = None

    def make_connection(self, host):
        if self.proxy_host:
            connection = http.client.HTTPSConnection(self.proxy_host, self.proxy_port)
            connection.set_tunnel(host)
            return connection
        else:
            # No proxy - use standard connection
            return super().make_connection(host)

    def send_headers(self, connection, headers):
        super().send_headers(connection, headers)
        # Bypass Koji's AI scraper detection
        connection.putheader("Accept", "text/xml")


def get_koji_server() -> xmlrpc.client.ServerProxy:
    """Get a Koji XML-RPC server proxy with proper transport configuration."""
    return xmlrpc.client.ServerProxy('https://koji.fedoraproject.org/kojihub',
                                      transport=KojiTransport(),
                                      allow_none=True)


def call_koji_with_retry(method, *args, method_name: str = "Koji method"):
    """Call a Koji XML-RPC method with retry logic for transient errors.

    Retries up to 3 times with exponential backoff (2s, 4s, 8s) on transient errors:
    - HTTP 5xx errors (server errors like 502, 503)
    - HTTP 408 (Request Timeout)
    - OSError and subclasses (ConnectionError, TimeoutError, socket errors)

    Does NOT retry on:
    - xmlrpc.client.Fault (server-side application errors)
    - HTTP 4xx errors except 408 (client errors like 401, 403)

    Args:
        method: The Koji ServerProxy method to call (e.g., server.getBuild)
        *args: Arguments to pass to the method
        method_name: Human-readable name for logging (e.g., "Koji getBuild(nvr)")

    Returns:
        The result from the Koji method call
    """
    max_retries = 3
    retry_delay = 2  # Initial delay in seconds

    for attempt in range(max_retries):
        try:
            return method(*args)
        except xmlrpc.client.Fault:
            # Don't retry on Fault - these are application-level errors
            raise
        except (OSError, http.client.HTTPException) as e:
            # Check if it's a retryable error
            should_retry = False
            error_msg = str(e)

            # Retry on HTTP 5xx errors, 408, and network errors
            if isinstance(e, http.client.HTTPException):
                # Parse HTTP status from error message
                if any(status in error_msg for status in ['502', '503', '504', '408']):
                    should_retry = True
                elif '5' in error_msg and any(word in error_msg.lower() for word in ['server', 'gateway', 'timeout']):
                    should_retry = True
            elif isinstance(e, (ConnectionError, TimeoutError, socket.error, OSError)):
                # Retry on network/connection errors
                should_retry = True

            # Check for 4xx errors that should not be retried
            if any(status in error_msg for status in ['401', '403', '404']):
                should_retry = False

            if not should_retry or attempt >= max_retries - 1:
                # Don't retry or last attempt failed
                raise

            # Retry with exponential backoff
            logging.warning("%s failed (attempt %d/%d): %s", method_name, attempt + 1, max_retries, e)
            logging.info("Retrying in %d seconds...", retry_delay)
            time.sleep(retry_delay)
            retry_delay *= 2  # Exponential backoff


class MdapiPackageInfo(TypedDict, total=False):
    """Result from MDAPI srcpkg endpoint."""
    version: str
    release: str


def get_mdapi_latest_build(package_name: str, branch: str) -> MdapiPackageInfo | None:
    """Get the latest build for a package from MDAPI.

    Queries the Fedora MDAPI (MetaSource) for package metadata.
    Returns None if no build found.
    """
    url = f'https://mdapi.fedoraproject.org/{branch}/srcpkg/{package_name}'

    try:
        output = subprocess.check_output(
            ['curl', '--silent', '--show-error', '--fail', '--retry', '3', '--max-time', '30', url],
            text=True
        )
        data = json.loads(output)
        return cast(MdapiPackageInfo, {'version': data['version'], 'release': data['release']})
    except subprocess.CalledProcessError:
        return None


def _get_fallback_dist_tags(dist_tag: str) -> list[str]:
    """Get all active Fedora dist tags except the given one, in descending order."""
    current_match = re.match(r'^f(\d+)$', dist_tag)
    if not current_match:
        return []

    current_num = int(current_match.group(1))
    fedora_releases = releases.get('fedora', {})
    other_nums = []
    for release_dist_tag in fedora_releases.values():
        if match := re.match(r'^f(\d+)$', release_dist_tag):
            num = int(match.group(1))
            if num != current_num:
                other_nums.append(num)

    other_nums.sort(reverse=True)
    return [f'f{n}' for n in other_nums]


def check_koji_build(package_name: str, version: str, release: str, expected_commit: str,
                     dist_tag: str, branch: str) -> bool:
    """Check if a build exists in Koji and matches the expected commit.

    If not found with current dist tag, tries all active Fedora releases in
    descending order as fallback. This handles packages like OpenJDK portables
    that are built once for the earliest active release and reused across all.
    """
    # Transform dist_tag for Koji: Bodhi uses f41, f42, etc. but Koji uses fc41, fc42
    koji_dist_tag = dist_tag
    if match := re.match(r'^f(\d+)$', dist_tag):
        koji_dist_tag = f'fc{match.group(1)}'

    # Construct NVR
    nvr = f'{package_name}-{version}-{release}.{koji_dist_tag}'

    server = get_koji_server()
    build_result = call_koji_with_retry(
        server.getBuild, nvr,
        method_name=f"Koji getBuild({nvr})"
    )

    # If build not found, try all active Fedora releases in descending order
    # Some packages (e.g. OpenJDK portables) are built for one release and reused
    if not build_result:
        fallback_dist_tags = _get_fallback_dist_tags(dist_tag)
        for fallback_tag in fallback_dist_tags:
            fallback_koji_tag = fallback_tag
            if match := re.match(r'^f(\d+)$', fallback_tag):
                fallback_koji_tag = f'fc{match.group(1)}'

            fallback_nvr = f'{package_name}-{version}-{release}.{fallback_koji_tag}'
            logging.info("Build %s not found in Koji, trying %s", nvr, fallback_nvr)
            nvr = fallback_nvr
            build_result = call_koji_with_retry(
                server.getBuild, fallback_nvr,
                method_name=f"Koji getBuild({fallback_nvr})"
            )
            if build_result:
                break

    if not build_result:
        logging.info("Build %s not found in Koji", nvr)
        return False

    build = cast(KojiBuild, build_result)

    # Extract commit from source field
    source = build.get('source', '')
    if '#' not in source:
        logging.warning("Build %s has no commit in source: %s", nvr, source)
        return False

    commit = source.split('#')[1]

    # Check if commit matches
    if commit != expected_commit:
        logging.warning("Build %s has different commit: %s (expected %s)",
                        nvr, commit[:8], expected_commit[:8])
        return False

    # Check build state (1 = COMPLETE)
    if build.get('state') != 1:
        logging.info("Build %s is not complete (state=%s)", nvr, build.get('state'))
        return False

    logging.info("Build %s found in Koji (commit %s)", nvr, commit[:8])
    return True


def is_package_unmodified(package_name: str, metadata: PackageMetadata, upstream_dir: Path) -> bool:
    """Check if package directory matches the imported version.

    Local changes that only affect the Release: field in the spec file
    (which we bump for rebuilds) are ignored.
    """
    package_dir = ROOT_DIR / 'rpms' / package_name

    # Clone upstream_dir as we are going to checkout specific sha and remove .git/ for comparison
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir) / package_name
        run_git('clone', '--quiet', '--branch', metadata['branch'], '--single-branch',
                str(upstream_dir), str(temp_dir))
        run_git('checkout', '--quiet', metadata['sha'], cwd=temp_dir)
        shutil.rmtree(temp_dir / '.git')

        # First, check all non-spec files are identical
        if subprocess.run(
            ['diff', '--recursive', '--ignore-trailing-space', '--ignore-blank-lines', '--exclude=*.spec', str(package_dir), str(temp_dir)],
            stdout=subprocess.DEVNULL,
        ).returncode != 0:
            return False

        # Then check including spec files, ignoring Release: lines
        # This checks all other files again, but dist-gits are small and this is very robust
        return subprocess.run(
            ['diff', '--recursive', '--ignore-trailing-space', '--ignore-blank-lines', '--ignore-matching-lines=^Release:', str(package_dir), str(temp_dir)],
            stdout=subprocess.DEVNULL,
        ).returncode == 0


def import_(url: str, branch: str, ref: str | None = None, directory: str | None = None,
            dry_run: bool = False) -> None:
    """Import a new dist-git package."""
    url = expand_url_shortcut(url)
    package_name = Path(url).stem

    # Allow overriding the directory name
    dir_name = directory if directory else package_name
    package_dir = ROOT_DIR / 'rpms' / dir_name

    if package_dir.exists():
        sys.exit(f"ERROR: Package directory rpms/{dir_name}/ already exists\n"
                 f"       Use 'sync' or 'update' command to update existing packages.")

    if dir_name != package_name:
        logging.info("Importing %s from %s to rpms/%s/ (branch: %s%s)",
                     package_name, url, dir_name, branch, f", ref: {ref}" if ref else "")
    else:
        if ref:
            logging.info("Importing %s from %s (branch: %s, ref: %s)", package_name, url, branch, ref)
        else:
            logging.info("Importing %s from %s (branch: %s)", package_name, url, branch)

    logging.info("Cloning from %s...", url)
    if ref:
        # Need full history to checkout specific ref
        run_git('clone', '--quiet', '--branch', branch, '--single-branch', url, str(package_dir))
        logging.info("Checking out ref: %s", ref)
        run_git('checkout', '--quiet', ref, cwd=package_dir)
    else:
        run_git('clone', '--quiet', '--branch', branch, '--depth=1', '--single-branch', url, str(package_dir))

    sha = run_git('rev-parse', 'HEAD', cwd=package_dir).stdout.strip()
    logging.info("Commit: %s", sha)

    # Parse spec file for version/release
    version, release = parse_spec_version(package_dir)

    # Resolve %autorelease if present
    if uses_autorelease(package_dir):
        resolved_release = query_autorelease_from_mdapi(package_name, branch, release)
        if resolved_release:
            release = resolved_release
            replace_autorelease_in_spec(package_dir, release)

    logging.info("Version: %s-%s", version, release)

    # We can't have sub .git directories in our repo
    shutil.rmtree(package_dir / '.git')

    # Save package metadata to import.json
    # The source URL contains the upstream package name, so we don't need to store it separately
    metadata: PackageMetadata = {
        'source': url,
        'branch': branch,
        'sha': sha,
        'version': version,
        'release': release,
    }

    # Set modification_status based on source
    if 'gitlab.com/redhat/hummingbird' in url:
        metadata['modification_status'] = 'native'
    else:
        metadata['modification_status'] = 'clean'

    save_package_metadata(dir_name, metadata)
    # Update global imports dict
    imports[dir_name] = metadata

    logging.info("Checking prerequisites...")
    check_jinja2_available()

    logging.info("Calling generate_resources.py to update Tekton resources...")
    subprocess.run([sys.executable, ROOT_DIR / 'ci/generate_resources.py', 'all'], check=True)

    logging.info("Successfully imported %s to rpms/%s/", package_name, dir_name)
    logging.warning(
        "upstream_repo must be added to metadata/%s.json before CI will pass.\n"
        "       Find the canonical upstream git repository and add it, or use the\n"
        "       /generate-package-map Cursor skill to populate it automatically.\n"
        "       If no upstream repo exists, use: https://src.fedoraproject.org/rpms/%s",
        dir_name, dir_name,
    )

    # Commit the changes
    if not dry_run:
        run_git('add', '-f', f'rpms/{dir_name}', f'metadata/{dir_name}.json',
                'konflux-templates', '.tekton', 'releng', cwd=ROOT_DIR)
        commit_msg = f"Import {package_name}-{version}-{release}\n\nBranch: {branch}\nUpstream: {sha}"
        run_git_commit('-m', commit_msg, cwd=ROOT_DIR)


def normalize_release_in_specs(directory: Path) -> None:
    """Normalize Release: lines in all .spec files to avoid merge conflicts."""
    for spec_file in directory.glob('*.spec'):
        content = spec_file.read_text()
        normalized = re.sub(r'^Release:.*$', 'Release: 0%{?dist}', content, flags=re.MULTILINE)
        spec_file.write_text(normalized)


def bump_release(current_release: str, upstream_release: str | None = None) -> str:
    """Bump release number using .N suffix pattern.

    If upstream_release is provided and matches current_release, this is the first
    rebuild of a Fedora package, so we append .1. Otherwise, if the release already
    has a .N suffix, we increment it.

    This handles the edge case where Fedora ships Release: 3.1%{?dist} - we need to
    know if the current 3.1 is from Fedora (should become 3.1.1) or from our previous
    rebuild of Fedora's 3 (should become 3.2).

    Examples:
        "3" -> "3.1"
        "3.1" -> "3.2" (if upstream_release is None or "3")
        "3.1" -> "3.1.1" (if upstream_release is "3.1" - first rebuild of Fedora's 3.1)
        "3.1.1" -> "3.1.2" (if upstream_release is "3.1" - second rebuild)
        "8.%{revision}" -> "8.%{revision}.1"

    Args:
        current_release: Current release string (without %{?dist})
        upstream_release: Upstream Fedora release (without %{?dist}), if known

    Returns:
        Bumped release string
    """
    # If current matches upstream exactly, this is first rebuild - append .1
    if upstream_release and current_release == upstream_release:
        return f"{current_release}.1"

    # Pattern: if ends with .N where N is a digit, increment N
    # Otherwise, append .1
    match = re.match(r'^(.+)\.(\d+)$', current_release)
    if match:
        base, num = match.groups()
        return f"{base}.{int(num) + 1}"
    else:
        return f"{current_release}.1"


def update_release_in_spec(package_dir: Path, new_release: str) -> None:
    """Update Release: line in spec file.

    Handles both simple numeric releases and macro-based releases that end with %{?dist}.

    Args:
        package_dir: Package directory containing spec file
        new_release: New release value (without %{?dist}) - only used for non-macro releases
    """
    spec_files = list(package_dir.glob('*.spec'))
    if len(spec_files) != 1:
        sys.exit(f"ERROR: Expected exactly one .spec file in {package_dir}")

    spec_file = spec_files[0]
    content = spec_file.read_text()

    # Check if Release contains %{?dist} or %{dist}
    # May have content after dist (e.g., unbound: "11.1%{?dist} %{?extra_version:-e %{extra_version}}")
    release_match = re.search(r'^Release:\s+(.*)%\{\??dist\}(.*)$', content, flags=re.MULTILINE)

    if release_match:
        release_before_dist = release_match.group(1)
        release_after_dist = release_match.group(2)  # May be empty string

        # Check if it contains macros (e.g., %{baserelease})
        if '%{' in release_before_dist:
            # Macro-based release - add/increment .N suffix before %{?dist}
            # Don't expand macros, just manipulate the string directly
            # Examples:
            #   %{baserelease}%{?dist} -> %{baserelease}.1%{?dist}
            #   %{?snapver:0.%{snapver}.}%{baserelease}%{?dist} -> %{?snapver:0.%{snapver}.}%{baserelease}.1%{?dist}
            #   %{baserelease}.1%{?dist} -> %{baserelease}.2%{?dist}
            #   11.1%{?dist} %{?extra:-e %{extra}} -> 11.2%{?dist} %{?extra:-e %{extra}}

            # Check if there's already a .N suffix
            suffix_match = re.match(r'^(.*)\.(\d+)$', release_before_dist)
            if suffix_match:
                # Already has .N suffix, increment it
                base = suffix_match.group(1)
                num = int(suffix_match.group(2))
                new_release_line = f'{base}.{num + 1}'
            else:
                # No .N suffix yet, add .1
                new_release_line = f'{release_before_dist}.1'

            # Reconstruct the full Release line with anything after dist preserved
            # Preserve the original whitespace after "Release:"
            new_content = re.sub(
                r'^Release:(\s+).*%\{\??dist\}.*$',
                lambda m: f'Release:{m.group(1)}{new_release_line}%{{?dist}}{release_after_dist}',
                content,
                flags=re.MULTILINE
            )
            spec_file.write_text(new_content)
            logging.info("Updated Release: to %s%%{?dist}%s in %s (macro-based)",
                        new_release_line, release_after_dist, spec_file.name)
            return

    # Check if Release uses macros but does NOT end with dist
    # These packages need manual handling (e.g., nodejs25 with %{node_release})
    release_match_no_dist = re.search(r'^Release:\s+.*%\{[^}]+\}', content, flags=re.MULTILINE)
    if release_match_no_dist and not release_match:
        sys.exit(
            f"ERROR: {spec_file.name} uses macros in Release field without %%{{?dist}}: {release_match_no_dist.group(0)}\n"
            f"This package requires manual rebuild - the simple rebuild command cannot handle complex macro systems."
        )

    # Standard case: no macros before dist
    # Preserve whitespace after "Release:", the dist macro variant, and any trailing content
    new_content = re.sub(
        r'^Release:(\s+).*?(%\{\??dist\})(.*)$',
        lambda m: f'Release:{m.group(1)}{new_release}{m.group(2)}{m.group(3)}',
        content,
        flags=re.MULTILINE
    )

    if new_content == content:
        sys.exit(f"ERROR: Failed to update Release: in {spec_file.name} - "
                 f"Release field missing %{{?dist}}. This should not happen; "
                 f"please investigate the spec file.")

    spec_file.write_text(new_content)
    logging.info("Updated Release: to %s%%{?dist} in %s", new_release, spec_file.name)


def merge_local_modifications(package_name: str, package_dir: Path, tmpdir: Path,
                              metadata: PackageMetadata, upstream_dir: Path,
                              new_sha: str) -> bool:
    """Merge local modifications with new upstream version using git merge.

    Uses git's 3-way merge to automatically handle conflicts with proper markers.
    Normalizes Release: lines temporarily to avoid conflicts, then removes the normalization.

    Returns:
        True if conflicts exist, False if merge succeeded cleanly
    """
    assert metadata.get('modification_status') == 'modified'
    assert 'branch' in metadata
    assert 'sha' in metadata
    assert 'source' in metadata

    old_sha = metadata['sha']

    logging.info("Updating %s: %s -> %s (merging local changes)", package_name, old_sha[:8], new_sha[:8])

    # Configure git for commits/rebases in merge workspace
    run_git('config', 'user.name', 'Hummingbird Bot', cwd=upstream_dir)
    run_git('config', 'user.email', 'bot@example.com', cwd=upstream_dir)

    # Checkout old upstream and create branch for our local modifications
    run_git('checkout', '--quiet', old_sha, cwd=upstream_dir)
    run_git('checkout', '--quiet', '-b', 'hummingbird-local', cwd=upstream_dir)

    # Normalize Release: in old upstream to avoid merge conflicts
    # This is temporary - we'll remove this commit later
    normalize_release_in_specs(upstream_dir)
    run_git('commit', '--allow-empty', '-a', '-m', 'Normalize Release (temporary)', cwd=upstream_dir)

    # First, remove everything except .git
    for item in upstream_dir.iterdir():
        if item.name != '.git':
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    # Copy our local files with our modifications
    shutil.copytree(package_dir, upstream_dir, dirs_exist_ok=True)

    # Normalize Release: in local files too
    normalize_release_in_specs(upstream_dir)

    # Commit our local modifications
    run_git('add', '-A', cwd=upstream_dir)
    status_result = run_git('status', '--porcelain', cwd=upstream_dir)
    assert status_result.stdout.strip(), "Expected local modifications but git status is clean"
    run_git('commit', '-m', 'Local Hummingbird modifications', cwd=upstream_dir)

    # Drop the previous "Normalize Release" commit, to leave only our actual modifications, with Release: untouched
    rebase_result = run_git('rebase', '--onto', old_sha, 'HEAD~1', cwd=upstream_dir, check=False)
    if rebase_result.returncode != 0:
        # Rebase can fail when Version and Release are close enough to share a diff hunk context,
        # causing the normalized Release context to not match old_sha. Fall back to applying local
        # modifications without Release normalization — Release may then conflict during the merge
        # step, but that is handled by the caller's conflict-resolution flow.
        logging.warning("Release normalization rebase failed for %s, falling back to direct diff", package_name)
        run_git('rebase', '--abort', cwd=upstream_dir, check=False)
        run_git('checkout', '--quiet', '-B', 'hummingbird-local', old_sha, cwd=upstream_dir)
        for item in upstream_dir.iterdir():
            if item.name != '.git':
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        shutil.copytree(package_dir, upstream_dir, dirs_exist_ok=True)
        run_git('add', '-A', cwd=upstream_dir)
        run_git('commit', '--allow-empty', '-m', 'Local Hummingbird modifications', cwd=upstream_dir)

    # Checkout new upstream (don't normalize - we want to keep its Release)
    run_git('checkout', '--quiet', '-b', 'new-upstream', new_sha, cwd=upstream_dir)

    # Merge our local modifications into new upstream
    merge_result = run_git('merge', 'hummingbird-local', cwd=upstream_dir, check=False)

    # Check for conflicts
    has_conflicts = merge_result.returncode != 0
    if has_conflicts:
        logging.warning("Merge has conflicts - manual resolution required")
    else:
        logging.info("Local modifications applied successfully")

    # Copy merged result (with or without conflict markers) back to package_dir
    shutil.rmtree(package_dir)
    shutil.rmtree(upstream_dir / '.git')
    shutil.copytree(upstream_dir, package_dir)

    return has_conflicts


def update(package_name: str, skip_build_check: bool = False, sync: bool = False,
           dry_run: bool = False, allow_prerelease: bool = False, mark: bool = False,
           ref: str | None = None, branch: str | None = None) -> None:
    """Update a single package from upstream.

    In update mode (not sync), local modifications are automatically merged with upstream changes.
    If the merge has conflicts, the operation fails.

    In sync mode, local modifications are discarded.

    If ref is provided, update to that specific commit instead of latest.

    If branch is provided, switch to that upstream branch and update from it."""
    if package_name not in imports:
        sys.exit(f"ERROR: Package {package_name} not found (missing metadata/{package_name}.json)")

    package_dir = ROOT_DIR / 'rpms' / package_name
    assert package_dir.exists()

    metadata = imports[package_name]

    # Block native packages from being updated (they have no upstream)
    status = metadata.get('modification_status', 'clean')
    if status == 'native':
        sys.exit(f"ERROR: Cannot update native package {package_name}\n")

    # Resolve effective branch: --branch override or current metadata branch
    old_branch = metadata['branch']
    if branch and branch != old_branch:
        branch_changed = True
        effective_branch = branch
        get_dist_tag(effective_branch)
        logging.info("Switching %s from branch %s to %s", package_name, old_branch, effective_branch)
    else:
        branch_changed = False
        effective_branch = old_branch

    # Extract the upstream package name from the source URL (for Koji queries)
    # This may differ from the directory name
    upstream_package_name = Path(metadata['source']).stem

    if ref:
        # When ref is provided, we'll verify it exists when we clone
        latest_sha = ref  # Will be resolved to full SHA during clone
    else:
        # Use ls-remote to get latest commit (fast, no clone needed)
        result = run_git('ls-remote', metadata['source'], effective_branch)
        if not result.stdout.strip():
            sys.exit(f"ERROR: Unable to query remote for {package_name}")
        latest_sha = result.stdout.split()[0]

    # Check if there's an update
    if latest_sha == metadata['sha'] and not branch_changed:
        if sync and not mark:
            sys.exit(f"ERROR: Package {package_name} is already at upstream {latest_sha[:8]}")
        if not mark:
            logging.info("Skipping %s: already up-to-date", package_name)
            return

        # Mark mode: verify package is actually unmodified and create empty commit
        with tempfile.TemporaryDirectory() as tmpdir:
            upstream_dir = Path(tmpdir) / package_name
            clone_args = ['clone', '--quiet', '--branch', effective_branch]
            if not branch_changed:
                clone_args.append('--single-branch')
            clone_args.extend([metadata['source'], str(upstream_dir)])
            run_git(*clone_args)

            if not is_package_unmodified(package_name, metadata, upstream_dir):
                sys.exit(f"ERROR: Cannot mark {package_name} as synced - package has modifications\n"
                        f"       Use 'sync' without --mark to discard local changes")

            # Parse spec file to get version-release for commit message
            version, release = parse_spec_version(upstream_dir)
            upstream_package_name = Path(metadata['source']).stem
            old_version, old_release = metadata['version'], metadata['release']

        # Reset modification status to clean
        metadata['modification_status'] = 'clean'
        if 'modification_reason' in metadata:
            del metadata['modification_reason']
        if branch_changed:
            metadata['branch'] = effective_branch

        # Create empty commit with proper Upstream: trailer
        if not dry_run:
            save_package_metadata(package_name, metadata)
            run_git('add', str(METADATA_DIR / f'{package_name}.json'), cwd=ROOT_DIR)
            if branch_changed:
                commit_msg = f"Sync {package_name} to {version}-{release} (mark, {old_branch} -> {effective_branch})\n\nUpstream: {latest_sha}"
            else:
                commit_msg = f"Sync {package_name} to {version}-{release} (mark)\n\nUpstream: {latest_sha}"
            run_git_commit('--allow-empty', '-m', commit_msg, cwd=ROOT_DIR)

        logging.info("Marked %s as synced (no changes, empty commit)", package_name)
        return

    # There's an update available
    logging.info("Update available for %s: %s -> %s", package_name, metadata['sha'][:8], latest_sha[:8])

    with tempfile.TemporaryDirectory() as tmpdir:
        upstream_dir = Path(tmpdir) / package_name
        clone_args = ['clone', '--quiet', '--branch', effective_branch]
        if not branch_changed:
            clone_args.append('--single-branch')
        clone_args.extend([metadata['source'], str(upstream_dir)])
        run_git(*clone_args)
        if branch_changed:
            # Create a local branch for the old branch so is_package_unmodified()
            # and merge_local_modifications() can reference the old SHA
            run_git('branch', old_branch, f'origin/{old_branch}', cwd=upstream_dir)
        if ref:
            logging.info("Checking out ref: %s", ref)
            run_git('checkout', '--quiet', ref, cwd=upstream_dir)
            # Get the actual commit SHA
            latest_sha = run_git('rev-parse', 'HEAD', cwd=upstream_dir).stdout.strip()
            logging.info("Commit: %s", latest_sha)

        # Parse spec file to get version-release
        version, release = parse_spec_version(upstream_dir)

        # Query MDAPI for autorelease if needed (but don't modify upstream_dir yet, it's a git repo)
        has_autorelease = uses_autorelease(upstream_dir)
        if has_autorelease:
            resolved_release = query_autorelease_from_mdapi(upstream_package_name, effective_branch, release)
            if resolved_release:
                release = resolved_release

        logging.info("Version: %s-%s", version, release)

        # Skip if upstream version is older than current version (unless sync or forward branch move)
        if not sync and not _is_forward_branch_move(branch_changed, old_branch, effective_branch):
            try:
                if Version(version) < Version(metadata['version']):
                    logging.warning(
                        "Skipping %s: upstream version %s is older than current %s",
                        package_name, version, metadata['version'])
                    return
            except InvalidVersion:
                logging.debug("Could not compare versions for %s, proceeding", package_name)

        # Check track_upstream constraint - skip if upstream version doesn't match prefix
        tv = metadata.get('track_upstream')
        if not sync and not branch_changed and tv and tv != 'latest':
            if not (version == tv or version.startswith(tv + '.')):
                logging.warning(
                    "Skipping %s: upstream version %s doesn't match tracked version %s",
                    package_name, version, tv)
                return

        # Check for pre-release version (unless sync, --allow-prerelease, or branch change)
        if not sync and not allow_prerelease and not branch_changed:
            is_pre, pattern = is_prerelease(version, release)
            if is_pre:
                logging.warning("Skipping %s: pre-release version detected - %s (version: %s-%s)",
                               package_name, pattern, version, release)
                logging.info("Use --allow-prerelease to override this check")
                return

        dist_tag = get_dist_tag(effective_branch)

        # Check if this version-release was built in Koji; syncing is a human thing,
        # assume they know what they are doing
        if not skip_build_check and not sync:
            logging.info("Checking Koji for build %s-%s-%s...", upstream_package_name, version, release)
            if not check_koji_build(upstream_package_name, version, release, latest_sha, dist_tag, effective_branch):
                logging.info("Skipping %s: %s-%s not built in Koji", package_name, version, release)
                return

        # Update mode: merge local modifications with upstream changes
        # Sync mode: discard local modifications
        has_modifications = not is_package_unmodified(package_name, metadata, upstream_dir)
        has_conflicts = False

        if not sync and metadata.get('modification_status') == 'modified' and has_modifications:
            has_conflicts = merge_local_modifications(package_name, package_dir, Path(tmpdir),
                                                     metadata, upstream_dir, latest_sha)
        else:
            # No modifications or sync mode - simple update
            action = "Syncing" if sync else "Updating"
            logging.info("%s %s: %s -> %s", action, package_name, metadata['sha'][:8], latest_sha[:8])
            shutil.rmtree(package_dir)
            shutil.rmtree(upstream_dir / '.git')
            shutil.copytree(upstream_dir, package_dir)

        # Replace %autorelease in package_dir (now safe, no longer a git repo)
        if has_autorelease:
            replace_autorelease_in_spec(package_dir, release)

        logging.info("Updated to %s-%s", version, release)

        # Capture old version-release for commit message
        old_version = metadata['version']
        old_release = metadata['release']

        # Update package metadata
        imports[package_name]['sha'] = latest_sha
        imports[package_name]['version'] = version
        imports[package_name]['release'] = release
        if branch_changed:
            imports[package_name]['branch'] = effective_branch

        if sync or not has_modifications:
            # Reset modification_status to clean after successful update/sync
            imports[package_name]['modification_status'] = 'clean'
            imports[package_name].pop('modification_reason', None)
        # In merge mode with local modifications, keep modification_status as 'modified'

        save_package_metadata(package_name, imports[package_name])

        # Commit the changes (or stage for manual resolution on conflict)
        if not dry_run:
            if has_conflicts:
                # Stage metadata but leave conflicted package files for manual resolution
                run_git('add', '-f', f'metadata/{package_name}.json', cwd=ROOT_DIR)

                # Save state for --continue
                verb = "Sync" if sync else "Update"
                branch_suffix = f" ({old_branch} -> {effective_branch})" if branch_changed else ""
                commit_msg = f"{verb} {package_name} from {old_version}-{old_release} to {version}-{release}{branch_suffix}\n\nUpstream: {latest_sha}"
                with open(UPDATE_STATE_FILE, 'w') as f:
                    json.dump({'package': package_name, 'commit_msg': commit_msg}, f)

                logging.warning(
                    "Resolve conflicts in rpms/%s/, then:\n"
                    "  ./ci/dist_git.py update --continue",
                    package_name)
                sys.exit(2)

            run_git('add', '-f', f'rpms/{package_name}', f'metadata/{package_name}.json', cwd=ROOT_DIR)
            verb = "Sync" if sync else "Update"
            branch_suffix = f" ({old_branch} -> {effective_branch})" if branch_changed else ""
            commit_msg = f"{verb} {package_name} from {old_version}-{old_release} to {version}-{release}{branch_suffix}\n\nUpstream: {latest_sha}"
            run_git_commit('-m', commit_msg, cwd=ROOT_DIR)


def continue_update(dry_run: bool = False) -> None:
    """Continue a conflicted update after manual conflict resolution.

    Reads the state file written by update() on conflict, verifies conflicts
    are resolved, stages the package files, and commits.
    """
    if not UPDATE_STATE_FILE.exists():
        sys.exit("ERROR: No update in progress. Nothing to continue.")

    with open(UPDATE_STATE_FILE) as f:
        state = json.load(f)

    package_name = state['package']
    commit_msg = state['commit_msg']
    package_dir = RPMS_DIR / package_name

    if not package_dir.exists():
        UPDATE_STATE_FILE.unlink()
        sys.exit(f"ERROR: Package directory rpms/{package_name}/ not found")

    # Check for remaining conflict markers
    for path in package_dir.rglob('*'):
        if path.is_file():
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            if '<<<<<<<' in content:
                sys.exit(f"ERROR: Unresolved conflicts in {path.relative_to(ROOT_DIR)}\n"
                         f"       Resolve all conflicts, then run: ./ci/dist_git.py update --continue")

    if not dry_run:
        run_git('add', '-f', f'rpms/{package_name}', f'metadata/{package_name}.json', cwd=ROOT_DIR)
        run_git_commit('-m', commit_msg, cwd=ROOT_DIR)
        UPDATE_STATE_FILE.unlink()
        logging.info("Committed resolved update for %s", package_name)
    else:
        logging.info("Dry run: would commit resolved update for %s", package_name)


def rebuild_package(package_name: str, reason: str, dry_run: bool = False) -> None:
    """Rebuild a package by bumping its Release field.

    This operation does NOT change the package's modification_status. Release-only
    changes are ephemeral and don't affect whether a package is considered modified
    vs clean. The modification_status tracks source-level changes (patches, spec
    modifications), not Release field bumps.

    Args:
        package_name: Package name to rebuild
        reason: Reason for rebuild (for commit message)
        dry_run: If True, modify files but don't commit

    Raises:
        SystemExit: If package not found or rebuild fails
    """
    # Validate package exists
    package_dir = RPMS_DIR / package_name
    metadata_file = METADATA_DIR / f'{package_name}.json'

    if not metadata_file.exists():
        sys.exit(f"ERROR: Package '{package_name}' not found (missing {metadata_file})")

    if not package_dir.exists():
        sys.exit(f"ERROR: Package directory '{package_dir}' not found")

    logging.info("Rebuilding %s: %s", package_name, reason)

    # Handle %autorelease if present (only for non-native packages)
    if uses_autorelease(package_dir):
        metadata = load_package_metadata(package_name)
        if not metadata:
            sys.exit(f"ERROR: Could not load metadata for {package_name}")
        # Native packages don't have 'source' field and can't query MDAPI
        if 'source' not in metadata:
            sys.exit(f"ERROR: Package {package_name} is native and uses %autorelease - "
                     f"cannot resolve via MDAPI. Please handle this rebuild manually.")
        else:
            branch = metadata.get('branch', 'rawhide')
            release = query_autorelease_from_mdapi(package_name, branch, '1')
            if not release:
                sys.exit(f"ERROR: Could not resolve %autorelease for {package_name}")
            replace_autorelease_in_spec(package_dir, release)

    # Parse current version and release
    version, current_release = parse_spec_version(package_dir)

    # Load metadata to get upstream release (for smart bumping)
    metadata = load_package_metadata(package_name)
    if not metadata:
        sys.exit(f"ERROR: Could not load metadata for {package_name}")

    # Get upstream release from metadata (for non-native packages only)
    # This allows us to distinguish between:
    # - Fedora ships 3.1 -> first rebuild should be 3.1.1
    # - We already rebuilt Fedora's 3 to 3.1 -> next rebuild should be 3.2
    # For native packages, don't pass upstream_release - they have no upstream
    upstream_release = metadata.get('release') if 'source' in metadata else None

    # Bump release with upstream awareness
    new_release = bump_release(current_release, upstream_release)
    logging.info("Bumping release: %s -> %s", current_release, new_release)

    # Update spec file
    update_release_in_spec(package_dir, new_release)

    # Commit the changes
    if not dry_run:
        run_git('add', '-f', f'rpms/{package_name}', cwd=ROOT_DIR)
        commit_msg = f"Rebuild {package_name}: {reason}"
        run_git_commit('-m', commit_msg, cwd=ROOT_DIR)
        logging.info("Committed rebuild for %s", package_name)
    else:
        logging.info("Dry-run mode: spec file modified but not committed")


def check_git_config() -> None:
    """Check that git user.name and user.email are configured."""

    name_result = run_git('config', 'user.name', cwd=ROOT_DIR, check=False)
    email_result = run_git('config', 'user.email', cwd=ROOT_DIR, check=False)

    if (name_result.returncode != 0 or not name_result.stdout.strip() or
        email_result.returncode != 0 or not email_result.stdout.strip()):
        sys.exit(
            "ERROR: Please configure git:\n"
            "   git config user.name 'Your Name'\n"
            "   git config user.email 'you@example.com'"
        )


def mark_modified(package_name: str, modified: bool, reason: str | None = None) -> None:
    """Mark a package as modified or clean.

    Args:
        package_name: Package name to mark
        modified: True to mark as modified, False to mark as clean
        reason: Reason for modification (required if modified=True)
    """
    if package_name not in imports:
        sys.exit(f"ERROR: Package {package_name} not found (missing metadata/{package_name}.json)")

    metadata = imports[package_name]

    if modified:
        # Mark as modified
        metadata['modification_status'] = 'modified'

        # Get reason (prompt if not provided)
        if not reason:
            try:
                reason = input("Reason for modification: ").strip()
                if not reason:
                    sys.exit("ERROR: Reason is required when marking as modified")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\nAborted")

        metadata['modification_reason'] = reason
        logging.info("Marked %s as modified: %s", package_name, reason)
    else:
        # Mark as clean
        metadata['modification_status'] = 'clean'
        metadata.pop('modification_reason', None)
        logging.info("Marked %s as clean (auto-updates enabled)", package_name)

    # Save updated metadata
    save_package_metadata(package_name, metadata)
    imports[package_name] = metadata


_UNSET = object()


def set_upstream(
    package_name: str,
    track_version=_UNSET,
    project_id=_UNSET,
) -> None:
    """Configure upstream tracking settings for a package.

    Each parameter uses a sentinel default (_UNSET) to mean "don't change".
    Pass an explicit value to set, or None to remove.

    Args:
        package_name: Package name to update
        track_version: "latest" enables tracking any version,
            a version prefix (e.g. "1.26") enables tracking that prefix,
            None removes tracking entirely
        project_id: int sets Anitya project ID for direct lookup,
            str sets upstream package name for name-based lookup,
            None removes the setting (reverts to RPM name)
    """
    if package_name not in imports:
        sys.exit(f"ERROR: Package {package_name} not found (missing metadata/{package_name}.json)")

    metadata = imports[package_name]

    if track_version is not _UNSET:
        if track_version is None:
            metadata.pop('track_upstream', None)
            logging.info("Disabled upstream version tracking for %s", package_name)
        else:
            metadata['track_upstream'] = track_version
            logging.info("Set track_upstream=%s for %s", track_version, package_name)

    if project_id is not _UNSET:
        if project_id is None:
            metadata.pop('release_monitoring_project_id', None)
            logging.info("Removed release_monitoring_project_id for %s", package_name)
        else:
            metadata['release_monitoring_project_id'] = project_id
            logging.info("Set release_monitoring_project_id=%s for %s", project_id, package_name)

    save_package_metadata(package_name, metadata)
    imports[package_name] = metadata


def diff_package(package_name: str, output_mode: str = 'full', raw: bool = False,
                 capture: bool = False) -> bool | str | None:
    """Show diff between local package and upstream Fedora.

    Args:
        package_name: Name of package to diff
        output_mode: 'full' (default), 'stat', or 'name-only'
        raw: If True, show raw diff with no filters
        capture: If True and output_mode='full', return diff text instead of printing

    Returns:
        When capture=False: True if differences exist, False if clean, None if native
        When capture=True: diff string if differences exist, '' if clean, None if native
    """
    if capture and output_mode != 'full':
        raise ValueError(f"capture=True is only supported with output_mode='full', got {output_mode!r}")

    metadata = load_package_metadata(package_name)
    if not metadata:
        sys.exit(f"ERROR: Package {package_name} not found")

    # Skip native packages
    if metadata.get('modification_status') == 'native':
        print(f"{package_name}: Native package (no upstream to diff against)")
        return None

    # Clone upstream to temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        upstream_dir = Path(tmpdir) / 'upstream'

        logging.info("Cloning %s from %s (branch: %s, SHA: %s)",
                    package_name, metadata['source'], metadata['branch'], metadata['sha'][:8])

        # Clone and checkout exact SHA
        run_git('clone', '--quiet', '--branch', metadata['branch'],
                '--single-branch', metadata['source'], str(upstream_dir))
        run_git('checkout', '--quiet', metadata['sha'], cwd=upstream_dir)

        # Remove .git to avoid comparing git metadata
        shutil.rmtree(upstream_dir / '.git')

        # Build diff command based on mode and filters
        diff_cmd = ['diff', '--recursive', '--unified', '--exclude=.git']

        if not raw:
            # Apply same filters as is_package_unmodified()
            diff_cmd.extend([
                '--ignore-trailing-space',
                '--ignore-blank-lines',
                '--ignore-matching-lines=^Release:',
            ])

        # Add mode-specific flags
        if output_mode == 'stat':
            # For stat mode, we need actual diff output to compute stats
            pass  # Will process output below
        elif output_mode == 'name-only':
            diff_cmd.append('--brief')

        diff_cmd.extend([str(upstream_dir), str(RPMS_DIR / package_name)])

        # Run diff and capture output
        result = subprocess.run(diff_cmd, capture_output=True, text=True)

        if result.returncode == 0:
            # No differences
            return '' if capture else False
        elif result.returncode == 1:
            # Differences found
            if capture and output_mode == 'full':
                return result.stdout
            if output_mode == 'name-only':
                # Parse --brief output to show only filenames
                for line in result.stdout.splitlines():
                    if line.startswith('Files '):
                        # Extract filename from "Files <upstream>/file and <local>/file differ"
                        parts = line.split(' and ')
                        if len(parts) == 2:
                            local_path = parts[1].split(' differ')[0]
                            filename = Path(local_path).name
                            print(filename)
                    elif line.startswith('Only in '):
                        # Handle files that exist in only one location
                        print(line)
            elif output_mode == 'stat':
                # Parse diff output to create stat-style summary
                # For simplicity, just use diffstat if available, otherwise show file list
                try:
                    stat_result = subprocess.run(['diffstat'], input=result.stdout,
                                                 capture_output=True, text=True, check=True)
                    print(stat_result.stdout)
                except (subprocess.CalledProcessError, FileNotFoundError):
                    # diffstat not available, fall back to simple file count
                    files_changed = set()
                    for line in result.stdout.splitlines():
                        if line.startswith('---') or line.startswith('+++'):
                            if '/dev/null' not in line:
                                files_changed.add(line.split('\t')[0][4:])  # Remove '--- ' or '+++ '
                    print(f"{len(files_changed)} file(s) changed")
            else:
                # Full diff output
                print(result.stdout)
            return True
        else:
            # Error occurred
            sys.exit(f"Error running diff: {result.stderr}")


def get_virtual_buildrequires_translation(package_name: str) -> str:
    """Translate virtual BuildRequires to actual target, or return package_name unchanged.

    Virtual BuildRequires packages (golang1.{25,26}, python3.{13,14}, etc.) all
    provide a common virtual package (golang, python3-devel).

    Dependency chain example for Go packages:
        go-fdo-client
          → BuildRequires: go-rpm-macros
            → Requires: golang (virtual package)
              → Provided by: golang1.25 (Provides: golang = 1.25.9-1)
              → Provided by: golang1.26 (Provides: golang = 1.26.2-1)
              → DNF picks the highest version match → golang1.26

    Since DNF always selects the latest version to satisfy the virtual package requirement,
    only changes to the LATEST version trigger rebuilds of reverse dependencies.
    Updating golang1.25 when golang1.26 exists won't affect any builds, so we reject it.

    Args:
        package_name: Package name to check (e.g., 'golang1.26' or 'openssl')

    Returns:
        Target package name (e.g., 'go-rpm-macros') if virtual BuildRequires and latest version,
        otherwise the original package_name unchanged

    Raises:
        SystemExit: If package is a virtual BuildRequires but not the latest
    """
    for pattern, target in VIRTUAL_BUILDREQUIRES_PATTERNS.items():
        match = re.match(pattern, package_name)
        if match:
            version_suffix = match.group(1)

            # Find all packages matching this pattern
            all_matching = [
                (pkg.name, m.group(1))
                for pkg in RPMS_DIR.glob('*')
                if pkg.is_dir() and (m := re.match(pattern, pkg.name))
            ]

            # Should always find at least the input package since we matched it above
            assert all_matching, f"No packages found matching pattern {pattern} despite matching {package_name}"

            # Find the latest version using proper version comparison
            latest_pkg, latest_suffix = max(all_matching, key=lambda x: Version(x[1]))

            if Version(version_suffix) < Version(latest_suffix):
                sys.exit(f"ERROR: {package_name} is not the latest version.\n"
                        f"       Latest version is {latest_pkg}.\n"
                        f"       Only the latest version triggers rebuilds of reverse dependencies.")

            logging.info("Translating %s to %s (virtual BuildRequires pattern)", package_name, target)
            return target

    return package_name


def expand_spec_buildrequires(package_dir: Path) -> list[str]:
    """Expand spec file macros and extract BuildRequires.

    Args:
        package_dir: Path to package directory containing spec file

    Returns:
        List of BuildRequires package names (without version constraints)
    """
    spec_files = list(package_dir.glob('*.spec'))
    assert len(spec_files) == 1, \
        f"Expected exactly one .spec file in {package_dir}, found {len(spec_files)}: {spec_files}"
    spec_file = spec_files[0]

    # Resolve macros and conditionals
    result = subprocess.run(
        ['rpmspec', '-q', '--buildrequires', f'--define=_sourcedir {package_dir}', str(spec_file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
    )

    # Parse BuildRequires - each line is a requirement
    # Format can be: "package", "package >= version", "package = version", etc.
    # Extract just the package name (before any version constraint)
    return [
        line.split()[0]
        for line in result.stdout.strip().splitlines()
        if line.strip()
    ]


def print_rebuild_summary(total_packages: int, failed_packages: list[str], title: str = "Rebuild Summary") -> None:
    """Print rebuild summary and exit if there are failures.

    Args:
        total_packages: Total number of packages attempted
        failed_packages: List of package names that failed to rebuild
        title: Title for the summary report
    """
    successful = total_packages - len(failed_packages)
    print(f"\n{title}:")
    print(f"  Total packages: {total_packages}")
    print(f"  Successful rebuilds: {successful}")
    print(f"  Failed rebuilds: {len(failed_packages)}")

    if failed_packages:
        print("\nFailed packages:")
        for pkg in failed_packages:
            print(f"  - {pkg}")
        sys.exit(1)


def rebuild_reverse_dependencies(package_name: str, reason: str, dry_run: bool = False) -> None:
    """Rebuild all reverse dependencies of a package.

    For virtual BuildRequires (golang1.X, python3.X), only rebuilds if the package
    is the latest version. Translates to the actual BuildRequires target (e.g.,
    golang1.26 → go-rpm-macros).

    Args:
        package_name: Package whose reverse dependencies should be rebuilt
        reason: Reason for rebuild
        dry_run: If True, don't commit changes
    """
    # Translate virtual BuildRequires to actual target (e.g., golang1.26 → go-rpm-macros)
    actual_target = get_virtual_buildrequires_translation(package_name)

    # Find all reverse dependencies
    logging.info("Scanning packages for BuildRequires: %s", actual_target)
    reverse_deps = []
    for package_dir in sorted(RPMS_DIR.glob('*')):
        if not package_dir.is_dir():
            continue

        pkg = package_dir.name
        buildrequires = expand_spec_buildrequires(package_dir)

        if actual_target in buildrequires:
            logging.debug("Found reverse dependency: %s", pkg)
            reverse_deps.append(pkg)

    if not reverse_deps:
        logging.info("No packages found with BuildRequires: %s", actual_target)
        return

    logging.info("Found %d reverse dependencies of %s", len(reverse_deps), actual_target)
    logging.info("Packages to rebuild: %s", ', '.join(sorted(reverse_deps)))

    # Rebuild each package
    failed_packages = []
    for pkg in reverse_deps:
        try:
            rebuild_package(pkg, reason, dry_run=dry_run)
        except SystemExit as e:
            logging.error("Failed to rebuild %s: %s", pkg, e.code if isinstance(e.code, str) else "error")
            failed_packages.append(pkg)

    # Report summary
    print_rebuild_summary(len(reverse_deps), failed_packages, f"Rebuild Summary for {package_name} reverse dependencies")


def list_packages(status_filter: str | None = None, prerelease_filter: bool = False, name_only: bool = False) -> None:
    """List packages with their modification status.

    Args:
        status_filter: Filter by status ('clean', 'modified', 'native', or None for all)
        prerelease_filter: If True, show only packages with pre-release versions
        name_only: If True, show only package names without formatting (useful for shell scripting)
    """
    metadata_files = sorted(METADATA_DIR.glob('*.json'))

    if not metadata_files:
        print("No packages found")
        return

    # Collect packages with their status
    packages: list[tuple[str, str, str | None, str]] = []  # (name, status, reason, version)

    for metadata_file in metadata_files:
        package_name = metadata_file.stem
        with open(metadata_file) as f:
            metadata: PackageMetadata = json.load(f)

        status = metadata.get('modification_status', 'unknown')
        reason = metadata.get('modification_reason')
        version = metadata.get('version', '')
        release = metadata.get('release', '')

        # Apply status filter if specified
        if status_filter and status != status_filter:
            continue

        # Apply prerelease filter if specified
        if prerelease_filter:
            is_pre, _ = is_prerelease(version, release)
            if not is_pre:
                continue

        packages.append((package_name, status, reason, version))

    if name_only:
        for name, _, _, _ in packages:
            print(name)
        return

    if not packages:
        if prerelease_filter:
            print("No pre-release packages found")
        elif status_filter:
            print(f"No {status_filter} packages found")
        else:
            print("No packages found")
        return

    # Print header
    if prerelease_filter:
        print(f"PRE-RELEASE PACKAGES ({len(packages)}):")
    elif status_filter:
        print(f"{status_filter.upper()} PACKAGES ({len(packages)}):")
    else:
        print(f"ALL PACKAGES ({len(packages)}):")
    print()

    # Print packages
    for name, status, reason, version in packages:
        status_indicator = {
            'clean': '✓',
            'modified': '⚠',
            'native': '●',
        }.get(status, '?')

        if prerelease_filter:
            # Show version for pre-release packages
            print(f"  {status_indicator} {name:<40} [{status}] v{version}")
        else:
            print(f"  {status_indicator} {name:<40} [{status}]")

        if reason and status == 'modified':
            print(f"    → {reason}")


def main() -> None:
    global imports, releases

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Load all package metadata from per-package import.json files
    imports = get_all_imported_packages()

    # Load upstream-releases.json
    releases = cast(dict[str, dict[str, str]], json.loads(RELEASES_JSON.read_text()))

    parser = argparse.ArgumentParser(
        description='Import and sync dist-git packages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import bash from rawhide (using shortcut)
  %(prog)s import fedora/bash

  # Import tomcat from rawhide into rpms/tomcat10/ directory
  %(prog)s import --directory tomcat10 fedora/tomcat

  # Import glibc from Fedora 42 (using full URL), don't commit the update
  %(prog)s --dry-run import --branch f42 https://src.fedoraproject.org/rpms/glibc.git

  # Import from CentOS Stream (using shortcut)
  %(prog)s import --branch c10s centos/kernel

  # Import CentOS Stream golang as golang-fips
  %(prog)s import --branch c10s --directory golang-fips centos/golang
"""
    )

    parser.add_argument('--dry-run', action='store_true',
                        help='Do not commit changes to git')
    parser.add_argument('-s', '--sign-off', action='store_true',
                        help='Add Signed-off-by trailer to commit messages')

    subparsers = parser.add_subparsers(dest='command', required=True)

    # import command
    import_parser = subparsers.add_parser('import', help='Import a new dist-git package')
    import_parser.add_argument('url', help='dist-git repository URL or shortcut (e.g., fedora/bash)')
    import_parser.add_argument('--branch', default='rawhide',
                               help='Branch to import from (default: rawhide)')
    import_parser.add_argument('--ref',
                               help='Specific commit/tag to import (default: latest on branch)')
    import_parser.add_argument('--directory',
                               help='Directory name in rpms/ (default: package name from URL)')

    # update command
    update_parser = subparsers.add_parser('update', help='Update packages from upstream (merges local modifications)')
    update_parser.add_argument('package', nargs='?', default=None,
                              help='Package name to update (default: all packages)')
    update_parser.add_argument('--skip-build-check', action='store_true',
                              help='Skip Koji build verification (for testing)')
    update_parser.add_argument('--allow-prerelease', action='store_true',
                              help='Allow updating to pre-release versions (rc, alpha, beta, dev, etc.)')
    update_parser.add_argument('--ref', type=str,
                              help='Update to specific commit/tag/ref instead of latest')
    update_parser.add_argument('--branch', type=str,
                              help='Switch to a different upstream branch and update from it (e.g., --branch f44)')
    update_parser.add_argument('--continue', action='store_true', dest='continue_',
                              help='Continue a conflicted update after resolving conflicts')

    # sync command
    sync_parser = subparsers.add_parser('sync', help='Force-sync package to upstream (discards local changes)')
    sync_parser.add_argument('package', help='Package name to sync')
    sync_parser.add_argument('--mark', action='store_true',
                            help='Mark package as synced even if already at upstream (creates empty commit with Upstream: trailer). Fails if package has actual modifications.')
    sync_parser.add_argument('--ref', type=str,
                            help='Sync to specific commit/tag/ref instead of latest')
    sync_parser.add_argument('--branch', type=str,
                            help='Switch to a different upstream branch and sync from it (e.g., --branch f44)')

    # rebuild command
    rebuild_parser = subparsers.add_parser('rebuild',
                                           help='Rebuild a package (bump Release field)')
    rebuild_parser.add_argument('packages', nargs='*', default=[],
                               help='Package name(s) to rebuild (mutually exclusive with --all)')
    rebuild_parser.add_argument('--all', action='store_true',
                               help='Rebuild all packages (one commit per package)')
    rebuild_parser.add_argument('--reason', required=True,
                               help='Reason for rebuild (e.g., "fix faulty build", "toolchain update")')

    # rebuild-rev-deps command
    rebuild_revdeps_parser = subparsers.add_parser('rebuild-rev-deps',
                                                   help='Rebuild all reverse dependencies of a package')
    rebuild_revdeps_parser.add_argument('package',
                                       help='Package name (e.g., golang1.26, python3.14, go-rpm-macros)')
    rebuild_revdeps_parser.add_argument('--reason', required=True,
                                       help='Reason for rebuild (e.g., "golang 1.26 update", "python ABI change")')

    # update-releases command
    subparsers.add_parser('update-releases',
                          help='Update upstream-releases.json from Bodhi API (rawhide auto-resolves to highest version)')

    # rename command
    rename_parser = subparsers.add_parser('rename', help='Rename a package')
    rename_parser.add_argument('package', help='Current package name (new name will be read from spec file)')

    # mark-modified command
    mark_parser = subparsers.add_parser('mark-modified',
                                       help='Mark a package as modified or clean')
    mark_parser.add_argument('package', help='Package name to mark')
    mark_group = mark_parser.add_mutually_exclusive_group(required=True)
    mark_group.add_argument('--modified', action='store_true',
                           help='Mark as modified (blocks auto-updates)')
    mark_group.add_argument('--clean', action='store_true',
                           help='Mark as clean (allows auto-updates)')
    mark_parser.add_argument('--reason', help='Reason for modification (required for --modified)')

    # set-upstream command
    upstream_parser = subparsers.add_parser('set-upstream',
                                            help='Configure upstream tracking settings for a package')
    upstream_parser.add_argument('package', help='Package name')
    tv_group = upstream_parser.add_mutually_exclusive_group()
    tv_group.add_argument('--track-version', type=str, default=None,
                          help='Version prefix to track (e.g., "1.26"), '
                               'or "latest" to track any version')
    tv_group.add_argument('--no-track-version', action='store_true',
                          help='Disable upstream version tracking')
    pid_group = upstream_parser.add_mutually_exclusive_group()
    pid_group.add_argument('--project-id', type=str, default=None,
                           help='Anitya project ID (integer) or upstream '
                                'package name (string) for release-monitoring.org lookup')
    pid_group.add_argument('--no-project-id', action='store_true',
                           help='Remove project ID (revert to RPM name lookup)')

    # list command
    list_parser = subparsers.add_parser('list',
                                       help='List packages with modification status')
    list_filter = list_parser.add_mutually_exclusive_group()
    list_filter.add_argument('--clean', action='store_true',
                            help='Show only clean packages')
    list_filter.add_argument('--modified', action='store_true',
                            help='Show only modified packages')
    list_filter.add_argument('--native', action='store_true',
                            help='Show only native packages')
    list_filter.add_argument('--prerelease', action='store_true',
                            help='Show only packages with pre-release versions')
    list_parser.add_argument('--name-only', action='store_true',
                            help='Show only package names (useful for shell scripting)')

    # diff command
    diff_parser = subparsers.add_parser('diff',
                                       help='Show differences between local and upstream packages')
    diff_parser.add_argument('packages', nargs='*',
                            help='Package names to diff (default: all modified packages if --all)')
    diff_parser.add_argument('--all', action='store_true',
                            help='Diff all modified packages')
    diff_mode = diff_parser.add_mutually_exclusive_group()
    diff_mode.add_argument('--stat', action='store_true',
                          help='Show only summary statistics (like git diff --stat)')
    diff_mode.add_argument('--name-only', action='store_true',
                          help='Show only names of changed files')
    diff_parser.add_argument('--raw', action='store_true',
                            help='Show raw diff without filters (includes Release:, whitespace)')

    ls_sources_parser = subparsers.add_parser('ls-sources', help='List contents of sources archives')
    ls_sources_parser.add_argument('package', help='Package name to inspect')

    args = parser.parse_args()

    # Set global sign-off flag
    global sign_off
    sign_off = args.sign_off

    # Reject --dry-run with rename (rename doesn't support dry-run)
    if args.command == 'rename' and args.dry_run:
        sys.exit("ERROR: --dry-run is not supported with the rename command")

    # Check git config for commands that will commit
    if args.command not in ['list', 'diff', 'set-upstream', 'ls-sources'] and (not args.dry_run or args.command == 'rename'):
        check_git_config()

    match args.command:
        case 'import':
            import_(args.url, args.branch, args.ref, args.directory, args.dry_run)
        case 'update':
            if args.continue_:
                continue_update(dry_run=args.dry_run)
            else:
                # Clean up stale state from a previous abandoned conflict
                if UPDATE_STATE_FILE.exists():
                    UPDATE_STATE_FILE.unlink()
                if args.branch and not args.package:
                    sys.exit("ERROR: --branch requires a package name (cannot change branch for all packages)")
                packages = [args.package] if args.package else list(imports.keys())
                for pkg in packages:
                    update(pkg, args.skip_build_check, dry_run=args.dry_run,
                           allow_prerelease=args.allow_prerelease, ref=args.ref,
                           branch=args.branch)
        case 'sync':
            update(args.package, sync=True, dry_run=args.dry_run, mark=args.mark,
                   ref=args.ref, branch=args.branch)
        case 'rebuild':
            # Validate mutually exclusive options
            if args.all and args.packages:
                sys.exit("ERROR: Cannot specify both package names and --all")
            if not args.all and not args.packages:
                sys.exit("ERROR: Must specify either package name(s) or --all")

            # Rebuild all packages or specified ones
            packages = list(imports.keys()) if args.all else args.packages
            failed_packages = []
            for pkg in packages:
                try:
                    rebuild_package(pkg, args.reason, dry_run=args.dry_run)
                except SystemExit as e:
                    if args.all or len(args.packages) > 1:
                        # In multi-package mode, track failures and continue
                        logging.error("Failed to rebuild %s: %s", pkg, e.code if isinstance(e.code, str) else "error")
                        failed_packages.append(pkg)
                    else:
                        # In single-package mode, re-raise to exit
                        raise

            # Report summary for multiple packages
            if args.all or len(args.packages) > 1:
                print_rebuild_summary(len(packages), failed_packages)
        case 'rebuild-rev-deps':
            rebuild_reverse_dependencies(args.package, args.reason, dry_run=args.dry_run)
        case 'update-releases':
            update_releases()
        case 'rename':
            rename(args.package)
        case 'mark-modified':
            mark_modified(args.package, args.modified, args.reason)
        case 'set-upstream':
            track_version = _UNSET
            if args.track_version is not None:
                track_version = args.track_version
            elif args.no_track_version:
                track_version = None

            project_id = _UNSET
            if args.project_id is not None:
                try:
                    project_id = int(args.project_id)
                except ValueError:
                    project_id = args.project_id
            elif args.no_project_id:
                project_id = None

            if track_version is _UNSET and project_id is _UNSET:
                sys.exit("ERROR: set-upstream requires at least one flag "
                         "(e.g. --track-version, --project-id)")
            set_upstream(
                args.package,
                track_version=track_version,
                project_id=project_id,
            )
        case 'list':
            # Determine filter based on flags
            status_filter = None
            if args.clean:
                status_filter = 'clean'
            elif args.modified:
                status_filter = 'modified'
            elif args.native:
                status_filter = 'native'
            list_packages(status_filter, prerelease_filter=args.prerelease, name_only=args.name_only)
        case 'diff':
            # Determine output mode
            output_mode = 'full'
            if args.stat:
                output_mode = 'stat'
            elif args.name_only:
                output_mode = 'name-only'

            # Determine which packages to diff
            if args.all:
                # Diff all modified packages
                packages = [
                    pkg for pkg, meta in imports.items()
                    if meta.get('modification_status') == 'modified'
                ]
                if not packages:
                    print("No modified packages found")
                    sys.exit(0)
            elif args.packages:
                packages = args.packages
            else:
                sys.exit("ERROR: Specify package names or use --all")

            # Diff each package
            has_diffs = False
            for pkg in packages:
                if len(packages) > 1:
                    print(f"\n=== {pkg} ===")

                result = diff_package(pkg, output_mode, args.raw)
                if result:
                    has_diffs = True

            # Exit with code 1 if any diffs found
            sys.exit(1 if has_diffs else 0)
        case 'ls-sources':
            ls_sources(args.package)


if __name__ == '__main__':
    main()
