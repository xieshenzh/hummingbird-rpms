#!/usr/bin/env python3
"""
Check that binary RPMs built from the current branch do not conflict with RPMs
already published to the Hummingbird Pulp repository.

Background: Konflux validates that a merge request *builds* successfully, but
does not check whether the resulting NEVR (Name-Epoch:Version-Release) has
already been published to Pulp. Since Pulp treats each NEVRA as unique and
immutable, two independent changes that happen to compute the same Release
(e.g. two no-change rebuilds based on stale `main`) can both pass CI and merge
without a git conflict, only for the post-merge build+sign+publish pipeline to
fail because the NEVRA already exists. By then the offending change has
already merged. This script catches that class of problem before merge.

Two modes:

  Spec mode (default): predict the NEVR(A) that would result from building
  each package's spec file, without actually building it. Uses `rpmspec` with
  the real dist-tag macros (%dist, %fedora, %hummingbird, etc.) extracted from
  rpms/hummingbird-release/hummingbird-release.spec -- the same macros a mock
  buildroot has installed at build time -- so the prediction matches a real
  build byte-for-byte, including for packages that embed %{?dist} inside a
  custom Release macro (e.g. krb5, kernel-headers; see
  documentation/operating/rebuilding-packages.md).

  Built-RPM mode (--rpms-dir): read the exact NEVRA from already-built RPM
  files (e.g. the output of `ci/build_rpms.sh`). No prediction needed --
  this is ground truth.

In spec mode, `rpmspec -q` lists a package entry for every declared Name:
and %package stanza, even ones with no bare %files section of their own
(e.g. krb5.spec, where every %files line is subpackage-qualified and the
base "krb5" binary is never actually produced -- roughly 14% of packages
in this repo have this shape). Such a phantom candidate can never really
be built or published, so it always reports OK; this is expected and
harmless noise, not a missed check. Crucially, it never overrides or
reduces the significance of a CONFLICT found elsewhere in the same run --
CONFLICT always reflects a real, already-published NEVR and should be
investigated regardless of how many (possibly phantom) OK results appear
alongside it. The CLI prints a reminder of this in spec mode whenever a
run has at least one OK result.

Only the public signed Pulp domain (public-hummingbird) is checked, since it
is readable anonymously over HTTP. Packages routed to a private product via
`private_product` in ci/package-overrides.yaml are still checked against the
public domain (in case they are also published there), but a note is printed
that their private domain was not verified, since it requires Pulp API
credentials this tool does not have.

This check reflects Pulp's state at the moment it runs. It substantially
reduces -- but cannot fully eliminate -- the race where another MR publishes
the same NEVR between this check passing and this MR's eventual merge; only a
hard check inside the actual Konflux publish pipeline (a separate repo,
quay.io/hummingbird-ci/rpmbuild-pipeline) could fully close that window.

Usage:
    # Check packages changed in the current MR (used in CI)
    ./ci/check_nevr_conflicts.py --mr

    # Check specific packages by name (spec mode, no build required)
    ./ci/check_nevr_conflicts.py curl openssl

    # Check every package in the repo
    ./ci/check_nevr_conflicts.py --all

    # Check actual built RPMs (ground truth) after ci/build_rpms.sh
    ./ci/build_rpms.sh curl --hermetic
    ./ci/check_nevr_conflicts.py --rpms-dir builds/curl

    # Machine-readable output
    ./ci/check_nevr_conflicts.py --mr --json

Exit codes:
    0 - no conflicts found
    1 - one or more NEVR conflicts found
    2 - usage or tooling error (missing rpmspec/dnf/rpm, bad spec, network
        failure querying Pulp, etc.) and no conflicts were found
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

# Reuse the existing MR-changed-package detection instead of duplicating it.
sys.path.insert(0, str(Path(__file__).parent))
from validate_package_modifications import get_changed_packages_in_mr

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / 'rpms'
PACKAGE_OVERRIDES_YAML = ROOT_DIR / 'ci' / 'package-overrides.yaml'
HUMMINGBIRD_RELEASE_SPEC = RPMS_DIR / 'hummingbird-release' / 'hummingbird-release.spec'

DEFAULT_BASE_URLS = {
    'x86_64': "https://packages.redhat.com/api/pulp-content/public-hummingbird/x86_64/",
    'aarch64': "https://packages.redhat.com/api/pulp-content/public-hummingbird/aarch64/",
    'source': "https://packages.redhat.com/api/pulp-content/public-hummingbird/source/",
}
BINARY_ARCHES = ('x86_64', 'aarch64')
DEBUGINFO_SUFFIXES = ('-debuginfo', '-debugsource')
DEFAULT_DNF_TIMEOUT_SECS = 30
DEFAULT_RPMSPEC_TIMEOUT_SECS = 20

# Matches the heredoc that hummingbird-release.spec writes to
# %{_rpmmacrodir}/macros.dist, after macro expansion via `rpmspec -P`.
MACROS_DIST_PATTERN = re.compile(
    r'^cat > \S*/macros\.dist << EOF\n(.*?)^EOF\s*$',
    re.MULTILINE | re.DOTALL,
)

# The built-in %{evr} tag already formats "[epoch:]version-release"
# consistently (omitting the epoch entirely when unset) in both rpm/rpmspec
# and dnf, so packages with no Epoch (the common case) compare equal without
# needing to special-case the literal string "(none)" that %{EPOCH} alone
# would print. Note dnf's queryformat engine does NOT support rpm's
# conditional %|TAG?{...}:{...}| syntax -- it passes it through unexpanded --
# so don't try to hand-roll epoch conditionals; %{evr} already does the
# right thing natively in both tools.
EVR_QUERYFORMAT = '%{EVR}'
DNF_EVR_QUERYFORMAT = '%{evr}'

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


class NevrCheckError(Exception):
    """Raised for tooling/usage errors: missing binaries, bad specs, timeouts."""


@dataclass(frozen=True)
class Candidate:
    """A binary or source RPM that would be produced by building a package."""

    package: str
    name: str
    evr: str
    arch: str  # x86_64, aarch64, noarch, or src


@dataclass
class CheckResult:
    candidate: Candidate
    status: str  # 'OK', 'CONFLICT', or 'CANNOT_DETERMINE'
    note: str = ''


# --------------------------------------------------------------------------
# Dist-tag macros
# --------------------------------------------------------------------------

def get_distro_macros() -> str:
    """Extract the resolved macros.dist content installed by hummingbird-release.

    This mirrors the macros a real mock buildroot has installed (%dist,
    %distcore, %hummingbird, %fedora, etc.) so that rpmspec queries run on
    the host, outside of mock, produce the exact same Release strings a real
    build would -- without this tool reimplementing %dist's Lua logic itself.
    """
    if not HUMMINGBIRD_RELEASE_SPEC.exists():
        raise NevrCheckError(f"Cannot find {HUMMINGBIRD_RELEASE_SPEC} to determine dist macros")

    try:
        result = subprocess.run(
            ['rpmspec', '-P', f'--define=_sourcedir {HUMMINGBIRD_RELEASE_SPEC.parent}',
             str(HUMMINGBIRD_RELEASE_SPEC)],
            capture_output=True, text=True, timeout=DEFAULT_RPMSPEC_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as e:
        raise NevrCheckError("Timed out parsing hummingbird-release.spec") from e
    except FileNotFoundError as e:
        raise NevrCheckError("rpmspec is required but not found in PATH") from e

    if result.returncode != 0:
        raise NevrCheckError(f"Failed to parse hummingbird-release.spec: {result.stderr.strip()}")

    match = MACROS_DIST_PATTERN.search(result.stdout)
    if not match:
        raise NevrCheckError(
            "Could not locate the macros.dist heredoc in hummingbird-release.spec output; "
            "has its %install section changed shape?"
        )
    return match.group(1)


def make_rpmmacros_home(macros_text: str) -> Path:
    """Create a temporary $HOME containing a .rpmmacros with macros_text.

    rpm's default macro search path includes ~/.rpmmacros, so pointing HOME
    at this directory when invoking rpmspec makes it pick up the dist macros
    without needing any --macros override (which would otherwise replace the
    entire default macro path).
    """
    tmp_home = Path(tempfile.mkdtemp(prefix='check-nevr-conflicts-home-'))
    (tmp_home / '.rpmmacros').write_text(macros_text)
    return tmp_home


# --------------------------------------------------------------------------
# Spec mode: predict NEVRs without building
# --------------------------------------------------------------------------

def _run_rpmspec(args: list[str], env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            ['rpmspec', *args], capture_output=True, text=True, env=env,
            timeout=DEFAULT_RPMSPEC_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as e:
        raise NevrCheckError(f"rpmspec timed out: {' '.join(args)}") from e
    except FileNotFoundError as e:
        raise NevrCheckError("rpmspec is required but not found in PATH") from e
    if result.returncode != 0:
        raise NevrCheckError(f"rpmspec failed for {args[-1]}: {result.stderr.strip()}")
    return result.stdout


def _spec_srpm(spec_file: Path, package_dir: Path, package: str, env: dict[str, str]) -> Candidate | None:
    """Resolve the SRPM's own name+EVR (arch-independent)."""
    out = _run_rpmspec(
        ['-q', '--qf', f'%{{NAME}}\t{EVR_QUERYFORMAT}\n',
         f'--define=_sourcedir {package_dir}', '--srpm', str(spec_file)],
        env,
    )
    line = next((line for line in out.strip().splitlines() if line.strip()), '')
    if not line or '\t' not in line:
        return None
    name, evr = line.split('\t', 1)
    return Candidate(package=package, name=name, evr=evr, arch='src')


def _spec_binaries(
    spec_file: Path, package_dir: Path, package: str, env: dict[str, str], target_arch: str,
) -> list[Candidate]:
    """Resolve binary subpackages as rpm would build them for target_arch.

    Running once per target arch (rather than relying on the host's default
    arch) is required because specs commonly gate subpackages behind
    %ifarch, so the set of binaries produced can differ by architecture.

    Note: rpmspec lists a package entry for the base Name: even when it has
    no bare %files section of its own (e.g. krb5.spec, where every %files
    line is subpackage-qualified and the base "krb5" binary is never
    actually produced). This harmlessly over-reports such phantom
    candidates as OK -- they can never be a real conflict, since they never
    get built or published -- rather than silently missing anything.
    """
    out = _run_rpmspec(
        ['-q', '--qf', f'%{{NAME}}\t{EVR_QUERYFORMAT}\t%{{ARCH}}\n',
         f'--define=_sourcedir {package_dir}', '--target', target_arch, str(spec_file)],
        env,
    )
    candidates = []
    for line in out.strip().splitlines():
        if not line.strip() or line.count('\t') != 2:
            continue
        name, evr, arch = line.split('\t')
        if arch.lower() == 'src':
            continue
        if name.lower().endswith(DEBUGINFO_SUFFIXES):
            continue
        candidates.append(Candidate(package=package, name=name, evr=evr, arch=arch))
    return candidates


def get_spec_candidates(package: str, rpmmacros_home: Path) -> list[Candidate]:
    """Predict all binary/source RPM NEVRAs that building `package` would produce."""
    package_dir = RPMS_DIR / package
    specs = sorted(package_dir.glob('*.spec'))
    if len(specs) != 1:
        raise NevrCheckError(f"{package}: expected exactly one .spec file in {package_dir}, found {len(specs)}")
    spec_file = specs[0]

    env = os.environ.copy()
    env['HOME'] = str(rpmmacros_home)

    candidates: dict[tuple[str, str], Candidate] = {}

    srpm = _spec_srpm(spec_file, package_dir, package, env)
    if srpm is not None:
        candidates[(srpm.name, srpm.arch)] = srpm

    for target_arch in BINARY_ARCHES:
        for candidate in _spec_binaries(spec_file, package_dir, package, env, target_arch):
            candidates[(candidate.name, candidate.arch)] = candidate

    if not candidates:
        raise NevrCheckError(f"{package}: rpmspec produced no binary or source packages")

    return sorted(candidates.values(), key=lambda c: (c.name, c.arch))


# --------------------------------------------------------------------------
# Built-RPM mode: read ground truth from already-built RPM files
# --------------------------------------------------------------------------

def _infer_package_name(rpm_path: Path) -> str:
    """Best-effort package-directory name for a built RPM, for reporting only."""
    for parent in rpm_path.parents:
        if parent.name in ('RPMS', 'SRPMS'):
            return parent.parent.name
    return rpm_path.stem


def get_built_rpm_candidates(rpms_dir: Path) -> list[Candidate]:
    """Read exact NEVRA from every .rpm file found (recursively) under rpms_dir."""
    if not rpms_dir.exists():
        raise NevrCheckError(f"--rpms-dir path does not exist: {rpms_dir}")

    candidates = []
    rpm_files = sorted(rpms_dir.rglob('*.rpm'))
    if not rpm_files:
        raise NevrCheckError(f"No .rpm files found under {rpms_dir}")

    for rpm_path in rpm_files:
        try:
            result = subprocess.run(
                ['rpm', '-qp', '--qf', f'%{{NAME}}\t{EVR_QUERYFORMAT}\t%{{ARCH}}\n', str(rpm_path)],
                capture_output=True, text=True, timeout=DEFAULT_RPMSPEC_TIMEOUT_SECS, check=True,
            )
        except subprocess.TimeoutExpired as e:
            raise NevrCheckError(f"rpm timed out reading {rpm_path}") from e
        except FileNotFoundError as e:
            raise NevrCheckError("rpm is required but not found in PATH") from e
        except subprocess.CalledProcessError as e:
            logging.warning("Could not read RPM header from %s: %s", rpm_path, e.stderr.strip())
            continue

        line = result.stdout.strip()
        if not line or line.count('\t') != 2:
            logging.warning("Unexpected rpm -qp output for %s: %r", rpm_path, line)
            continue
        name, evr, arch = line.split('\t')
        if name.lower().endswith(DEBUGINFO_SUFFIXES):
            continue
        candidates.append(Candidate(package=_infer_package_name(rpm_path), name=name, evr=evr, arch=arch))

    return candidates


# --------------------------------------------------------------------------
# Pulp comparison
# --------------------------------------------------------------------------

def repo_keys_for_candidate(candidate: Candidate) -> list[str]:
    """Which Pulp repo(s) (by arch key) a candidate's NEVRA would be published to."""
    if candidate.arch == 'noarch':
        return ['x86_64', 'aarch64']
    if candidate.arch in BINARY_ARCHES:
        return [candidate.arch]
    if candidate.arch == 'src':
        return ['source']
    return []


def query_pulp_evrs(base_url: str, names: set[str], timeout: int) -> dict[str, set[str]] | None:
    """Return {name: {evr, ...}} of every published EVR for `names` in one Pulp repo.

    Returns None (distinct from an empty dict) if the query itself failed
    (network/tooling problem), so callers can tell "confirmed not published"
    apart from "could not determine".
    """
    if not names:
        return {}

    repoid = 'nevrcheck'
    cmd = [
        'dnf', '-q',
        '--repofrompath', f'{repoid},{base_url}',
        '--repo', repoid,
        'repoquery', '--qf', f'%{{name}}\t{DNF_EVR_QUERYFORMAT}\n',
        *sorted(names),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.error("Timed out querying %s for: %s", base_url, ', '.join(sorted(names)))
        return None
    except FileNotFoundError:
        raise NevrCheckError("dnf is required but not found in PATH")

    if result.returncode != 0 and not result.stdout.strip():
        logging.error("dnf repoquery against %s failed: %s", base_url, result.stderr.strip())
        return None

    evrs: dict[str, set[str]] = {}
    for line in result.stdout.strip().splitlines():
        if not line.strip() or '\t' not in line:
            continue
        name, evr = line.split('\t', 1)
        evrs.setdefault(name, set()).add(evr)
    return evrs


def check_candidates(
    candidates: list[Candidate], base_urls: dict[str, str], timeout: int,
) -> list[CheckResult]:
    """Query Pulp once per repo (batched across all candidate names) and compare."""
    names_by_repo: dict[str, set[str]] = {'x86_64': set(), 'aarch64': set(), 'source': set()}
    for candidate in candidates:
        for repo_key in repo_keys_for_candidate(candidate):
            names_by_repo[repo_key].add(candidate.name)

    evrs_by_repo: dict[str, dict[str, set[str]] | None] = {}
    for repo_key, names in names_by_repo.items():
        if not names:
            evrs_by_repo[repo_key] = {}
            continue
        evrs_by_repo[repo_key] = query_pulp_evrs(base_urls[repo_key], names, timeout)

    results = []
    for candidate in candidates:
        repo_keys = repo_keys_for_candidate(candidate)
        if not repo_keys:
            results.append(CheckResult(candidate, 'CANNOT_DETERMINE', f"unrecognized arch {candidate.arch!r}"))
            continue

        conflict_repo = None
        any_unknown = False
        for repo_key in repo_keys:
            repo_evrs = evrs_by_repo.get(repo_key)
            if repo_evrs is None:
                any_unknown = True
                continue
            if candidate.evr in repo_evrs.get(candidate.name, set()):
                conflict_repo = repo_key
                break

        if conflict_repo is not None:
            results.append(CheckResult(candidate, 'CONFLICT', f"already published in {conflict_repo}"))
        elif any_unknown:
            results.append(CheckResult(candidate, 'CANNOT_DETERMINE', "Pulp query failed"))
        else:
            results.append(CheckResult(candidate, 'OK'))

    return results


# --------------------------------------------------------------------------
# private_product handling
# --------------------------------------------------------------------------

def load_private_products() -> dict[str, str]:
    """Return {package: private_product} for packages routed to a private repo."""
    if not PACKAGE_OVERRIDES_YAML.exists():
        return {}
    overrides = yaml.safe_load(PACKAGE_OVERRIDES_YAML.read_text()) or {}
    return {
        pkg: cfg['private_product']
        for pkg, cfg in overrides.items()
        if isinstance(cfg, dict) and cfg.get('private_product')
    }


# --------------------------------------------------------------------------
# Package selection
# --------------------------------------------------------------------------

def has_spec(package: str) -> bool:
    package_dir = RPMS_DIR / package
    return package_dir.is_dir() and any(package_dir.glob('*.spec'))


def get_all_packages() -> list[str]:
    return sorted(p.name for p in RPMS_DIR.iterdir() if has_spec(p.name))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that binary RPMs built from the current branch do not "
                    "conflict with RPMs already published to Pulp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check packages changed in the current MR (used in CI)
  %(prog)s --mr

  # Check specific packages (spec mode, no build required)
  %(prog)s curl openssl

  # Check every package in the repo
  %(prog)s --all

  # Check actual built RPMs (ground truth)
  %(prog)s --rpms-dir builds/curl
""",
    )
    parser.add_argument('packages', nargs='*', help='Package names to check (spec mode)')
    parser.add_argument('--mr', action='store_true', help='Check only packages changed in the current MR')
    parser.add_argument('--all', action='store_true', help='Check all packages under rpms/')
    parser.add_argument(
        '--rpms-dir', action='append', default=[], metavar='DIR',
        help='Check actual built RPM files under DIR instead of predicting from spec '
             '(e.g. builds/<pkg>, the output of ci/build_rpms.sh). May be given more than once.',
    )
    parser.add_argument('--base-url', default=os.environ.get('BASE_URL', DEFAULT_BASE_URLS['x86_64']),
                       help='Override the x86_64 Pulp repo base URL')
    parser.add_argument('--base-url-aarch64', default=os.environ.get('BASE_URL_AARCH64', DEFAULT_BASE_URLS['aarch64']),
                       help='Override the aarch64 Pulp repo base URL')
    parser.add_argument('--base-url-source', default=os.environ.get('BASE_URL_SOURCE', DEFAULT_BASE_URLS['source']),
                       help='Override the source Pulp repo base URL')
    parser.add_argument('--timeout', type=int,
                       default=int(os.environ.get('DNF_TIMEOUT_SECS', DEFAULT_DNF_TIMEOUT_SECS)),
                       help='Timeout in seconds for each Pulp query')
    parser.add_argument('--json', action='store_true', help='Output machine-readable JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    base_urls = {'x86_64': args.base_url, 'aarch64': args.base_url_aarch64, 'source': args.base_url_source}

    candidates: list[Candidate] = []
    errors: list[str] = []
    spec_mode = not args.rpms_dir

    if args.rpms_dir:
        for rpms_dir in args.rpms_dir:
            try:
                candidates.extend(get_built_rpm_candidates(Path(rpms_dir)))
            except NevrCheckError as e:
                errors.append(str(e))
    else:
        if args.mr:
            packages = [p for p in get_changed_packages_in_mr() if has_spec(p)]
        elif args.all:
            packages = get_all_packages()
        elif args.packages:
            packages = list(args.packages)
        else:
            parser.error("Must specify --mr, --all, --rpms-dir, or package names")

        if not packages:
            print("No packages to check")
            return 0

        try:
            rpmmacros_home = make_rpmmacros_home(get_distro_macros())
        except NevrCheckError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        try:
            for package in packages:
                try:
                    candidates.extend(get_spec_candidates(package, rpmmacros_home))
                except NevrCheckError as e:
                    errors.append(str(e))
        finally:
            shutil.rmtree(rpmmacros_home, ignore_errors=True)

    if not candidates and not errors:
        print("No binary or source packages found to check")
        return 0

    results = check_candidates(candidates, base_urls, args.timeout) if candidates else []
    private_products = load_private_products()
    conflicts = [r for r in results if r.status == 'CONFLICT']
    unknown = [r for r in results if r.status == 'CANNOT_DETERMINE']
    ok_count = len(results) - len(conflicts) - len(unknown)

    if args.json:
        payload = {
            'results': [
                {
                    'package': r.candidate.package,
                    'name': r.candidate.name,
                    'evr': r.candidate.evr,
                    'arch': r.candidate.arch,
                    'status': r.status,
                    'note': r.note,
                    'private_product': private_products.get(r.candidate.package),
                }
                for r in results
            ],
            'errors': errors,
        }
        print(json.dumps(payload, indent=2))
    else:
        packages_with_private_note: set[str] = set()
        for r in results:
            label = f"{r.candidate.name}-{r.candidate.evr}.{r.candidate.arch}"
            suffix = f" ({r.note})" if r.note else ''
            print(f"{r.status:<16} {r.candidate.package}: {label}{suffix}")
            if r.candidate.package in private_products:
                packages_with_private_note.add(r.candidate.package)

        for pkg in sorted(packages_with_private_note):
            print(f"NOTE: {pkg} is routed to private_product={private_products[pkg]!r}; "
                  "its private Pulp domain was not checked (requires Pulp API credentials)")

        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)

        print()
        print(f"Checked: {len(results)}  OK: {ok_count}  "
              f"Conflicts: {len(conflicts)}  Undetermined: {len(unknown)}  Errors: {len(errors)}")

        if spec_mode and ok_count > 0:
            print()
            print(
                "Note: in spec mode, some OK results above may be for a package name a real "
                "build never actually produces (e.g. a base package with no top-level %files "
                "section -- expected and harmless). This never overrides or reduces the "
                "significance of any CONFLICT above, which always reflects a real, "
                "already-published NEVR and should be investigated regardless."
            )

    if conflicts:
        return 1
    if unknown or errors:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
