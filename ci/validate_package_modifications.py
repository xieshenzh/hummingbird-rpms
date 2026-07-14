#!/usr/bin/env python3
"""
CI validation script for package modification tracking.

This script validates that the modification_status in metadata files
accurately reflects the actual state of packages.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Import from dist_git.py
sys.path.insert(0, str(Path(__file__).parent))
from dist_git import (
    METADATA_DIR,
    RPMS_DIR,
    ROOT_DIR,
    PackageMetadata,
    is_package_unmodified,
    run_git,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def get_changed_packages_in_mr() -> list[str]:
    """Get list of packages that were modified in the current MR/branch."""
    # Get the target branch (usually 'main')
    target_branch = os.environ.get('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', 'main')

    # Get changed files in rpms/ or metadata/
    result = run_git('diff', '--name-only', f'origin/{target_branch}...HEAD', cwd=ROOT_DIR)

    changed_packages = set()
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue

        # Check if file is in rpms/ or metadata/
        if line.startswith('rpms/'):
            # Extract package name from rpms/<package>/...
            parts = line.split('/')
            if len(parts) >= 2:
                changed_packages.add(parts[1])
        elif line.startswith('metadata/') and line.endswith('.json'):
            # Extract package name from metadata/<package>.json
            package = Path(line).stem
            changed_packages.add(package)

    return sorted(changed_packages)


def find_last_sync_commit(package_name: str) -> str | None:
    """
    Find the last Sync or Import commit for a package.

    Args:
        package_name: Package name to check

    Returns:
        SHA of the last Sync/Import commit, or None if not found
    """
    # Find the last Sync or Import commit for this package
    # Note: Sync commits with --mark are empty commits, so we can't filter by path
    # We search for "Sync <package>" or "Import <package>" in the subject line
    # Multiple --grep flags are OR'd by default in git log
    # Require version to start with digit to avoid prefix matches (e.g., "rust" matching "rust-podman-sequoia")
    result = run_git(
        'log',
        '--format=%H',
        '--grep',
        f'^Sync {package_name} ',
        '--grep',
        f'^Import {package_name}-[0-9]',
        cwd=ROOT_DIR,
        check=False
    )

    if result.stdout.strip():
        # Take the first (most recent) Sync/Import commit for this package
        return result.stdout.strip().split('\n')[0]
    return None


def is_release_only_commit(commit_sha: str, package_name: str) -> bool:
    """Check if a commit only changes Release: fields in spec files.

    Release-only commits are not considered modifications per project policy.
    This implements the same logic as is_package_unmodified() in dist_git.py.

    Args:
        commit_sha: The commit SHA to check
        package_name: Package name

    Returns:
        True if commit only changes Release: lines, False otherwise
    """
    package_path = f'rpms/{package_name}'

    # Get the diff for this commit, ignoring whitespace and Release: lines
    # Similar to is_package_unmodified() logic in dist_git.py
    result = run_git(
        'show',
        '--format=',  # Don't show commit message
        '-b',  # Ignore changes in amount of whitespace
        '--ignore-blank-lines',
        '-I', '^Release:',  # Ignore Release: field in spec file
        '-I', '^%.*[rR]elease',  # Ignore release macros (%global baserelease, etc.)
        commit_sha,
        '--',
        package_path,
        cwd=ROOT_DIR,
        check=False
    )

    # If diff is empty after ignoring Release: lines, it's a release-only commit
    return not result.stdout.strip()


def check_git_history_state(package_name: str, last_sync_sha: str | None) -> tuple[bool, str | None]:
    """
    Check if package is clean based on git commit history.

    A package is considered "clean" if all commits since the last Sync
    (or all commits if no Sync exists) have the "Upstream:" trailer.

    Release-only commits (commits that only modify Release: fields) are
    ignored per project policy.

    Uses git's native filtering for speed:
    - Uses --grep to find commits missing "Upstream:" trailer
    - Filters out release-only commits

    Args:
        package_name: Package name to check
        last_sync_sha: SHA of last Sync/Import commit, or None

    Returns:
        (is_clean, error_message) tuple
    """
    package_path = f'rpms/{package_name}'

    # Find commits without "Upstream:" trailer
    # Range: from HEAD to last Sync (exclusive), or all commits if no Sync
    if last_sync_sha:
        # Check commits from HEAD to last Sync (not including the Sync itself)
        git_range = f'{last_sync_sha}..HEAD'
    else:
        # No Sync found, check all commits
        git_range = 'HEAD'

    result = run_git(
        'log',
        '--format=%H %s',
        '--invert-grep',
        '--grep=^Upstream:',
        git_range,
        '--',
        package_path,
        cwd=ROOT_DIR,
        check=False
    )

    # If any commits found, package has modifications
    bad_commits = [line for line in result.stdout.strip().split('\n') if line]

    # historical accident: this commit removed unbound, e729d20ec3c2e9 reintroduced it and
    # landed in parallel with this validation. Ignore it.
    bad_commits = [line for line in bad_commits if not line.startswith('e5066a9dca178d5eeba0b1bfdacec5269314e99e ')]

    # chunkah backport used "Backport-of:" trailer instead of "Upstream:". The backport
    # has since been superseded by the 0.3.1 update.
    bad_commits = [line for line in bad_commits if not line.startswith('fbd9bdab64f786daada3e54ae795936e8d822075 ')]

    # glibc OPEN_TREE_* backport (bug 33921) superseded by upstream 2.42-14.
    bad_commits = [line for line in bad_commits if not line.startswith('1edd3cab10e1047d6374e2233ede46443776f3c9 ')]

    # xmlsec1 update to 1.2.42 to fix NSPR 4.38+ build failure was committed without
    # the Upstream: trailer before the policy was fully enforced.
    bad_commits = [line for line in bad_commits if not line.startswith('57730ec76cb2fb006c2d4f93550895e242309c38 ')]

    # openscap xmlsec1 >= 1.3 segfault fix was committed without the Upstream: trailer.
    # The fix has since been superseded by upstream Fedora openscap 1.4.4-4 (2360.patch).
    bad_commits = [line for line in bad_commits if not line.startswith('134c5f3502ca505092f7d000388821ea375a491d ')]

    # openssh /sbin/nologin move to openssh-server was committed locally and later
    # incorporated upstream in Fedora's 10.3p1-3 release.
    bad_commits = [line for line in bad_commits if not line.startswith('f0c23ce5ebcfd6a2b9bb0c4ff359e34059a7a724 ')]

    # glibc temporary bootstrap: removed -fno-link-libatomic while GCC 16 was unavailable.
    # Reverted once GCC 16 published to Pulp.
    bad_commits = [line for line in bad_commits if not line.startswith('5c111d84c07cc9ca1e8ae714f19687f47a5d4051 ')]

    if bad_commits:
        # Filter out release-only commits (per project policy, Release-only changes
        # are not considered modifications)
        non_release_commits = []
        for commit_line in bad_commits:
            parts = commit_line.split(' ', 1)
            sha = parts[0]

            if not is_release_only_commit(sha, package_name):
                non_release_commits.append(commit_line)

        if non_release_commits:
            # Report the first problematic commit
            parts = non_release_commits[0].split(' ', 1)
            sha = parts[0]
            subject = parts[1] if len(parts) == 2 else '(no subject)'

            return False, (
                f"{package_name}: Commit {sha[:8]} ('{subject}') is missing "
                f"'Upstream:' trailer. Package appears to be locally modified."
            )

    return True, None


def check_local_modifications(package_name: str, last_sync_sha: str | None) -> tuple[bool, str | None]:
    """
    Check local modifications for policy compliance.

    Checks:
    1. No additions to %changelog section
    2. Release bumps by 0.1 (not by 1) for local modifications

    Only checks commits after 2026-03-05 (when the rules were introduced).

    Args:
        package_name: Package name to check
        last_sync_sha: SHA of last Sync commit, or None if package was never synced

    Returns:
        (is_valid, error_message) tuple
    """
    package_path = f'rpms/{package_name}'

    # Find spec files
    spec_files = list((RPMS_DIR / package_name).glob('*.spec'))
    if not spec_files:
        return True, None  # No spec file, nothing to check

    # Find commits without "Upstream:" trailer (i.e., our local modifications)
    # Only check commits on or after 2026-03-05 (when the rules were introduced)
    # Range: from HEAD to last Sync (exclusive), or all commits if no Sync
    if last_sync_sha:
        git_range = f'{last_sync_sha}..HEAD'
    else:
        # No Sync found, check all commits
        git_range = 'HEAD'

    result = run_git(
        'log',
        '--format=%H',
        '--since=2026-03-05',  # Only commits on or after this date
        '--invert-grep',
        '--grep=^Upstream:',
        git_range,
        '--',
        package_path,
        cwd=ROOT_DIR,
        check=False
    )

    local_commits = [sha for sha in result.stdout.strip().split('\n') if sha]
    if not local_commits:
        # No local commits after cutoff date, nothing to check
        return True, None

    # Check each local commit for policy violations
    for spec_file in spec_files:
        spec_path = f'{package_path}/{spec_file.name}'

        for commit_sha in local_commits:
            # Check if this spec file was added or renamed in this commit
            # We don't want to flag %changelog in newly added/renamed files
            status_result = run_git(
                'show',
                '--name-status',
                '--format=',
                commit_sha,
                '--',
                spec_path,
                cwd=ROOT_DIR,
                check=False
            )

            # Status line format: "A\tpath" or "R100\told\tnew" or "M\tpath"
            if status_result.stdout.strip():
                status_line = status_result.stdout.strip().split()[0]
                if status_line.startswith('A') or status_line.startswith('R'):
                    # File was added or renamed, skip %changelog check
                    continue

            # Get the diff introduced by this commit
            result = run_git(
                'show',
                commit_sha,
                '--',
                spec_path,
                cwd=ROOT_DIR,
                check=False
            )

            if not result.stdout.strip():
                # This commit didn't touch this spec file
                continue

            # Check for policy violations in hunks
            for hunk in result.stdout.split('\n@@'):
                # Check 1: No additions to %changelog section
                if '%changelog' in hunk:
                    in_changelog = False
                    for line in hunk.split('\n'):
                        # Track when we've passed the %changelog marker
                        if '%changelog' in line and not line.startswith('-'):
                            in_changelog = True
                            continue
                        if in_changelog and line.startswith('+') and not line.startswith('+++'):
                            # Found an addition after %changelog
                            subject_result = run_git('log', '--format=%s', '-n1', commit_sha, cwd=ROOT_DIR)
                            subject = subject_result.stdout.strip()
                            return False, (
                                f"{package_name}: Commit {commit_sha[:8]} ('{subject}') added to "
                                f"%changelog section in {spec_file.name}. Do not add %changelog entries "
                                f"to avoid merge conflicts with Fedora updates."
                            )

                # Check 2: Release bumps must use 0.1 increments
                if 'Release:' in hunk:
                    for line in hunk.split('\n'):
                        if line.startswith('+Release:'):
                            # Extract release value, remove %{?dist} suffix
                            release_value = line.split(':')[1].strip().replace('%{?dist}', '').strip()
                            try:
                                # Check if it's an integer (bad) vs decimal (good)
                                val = float(release_value)
                                if val == int(val):  # It's an integer like 5.0
                                    subject_result = run_git('log', '--format=%s', '-n1', commit_sha, cwd=ROOT_DIR)
                                    subject = subject_result.stdout.strip()
                                    return False, (
                                        f"{package_name}: Commit {commit_sha[:8]} ('{subject}') sets "
                                        f"Release to {int(val)} in {spec_file.name}. Local modifications must bump "
                                        f"Release by 0.1 (e.g., {int(val) - 1}.1) to avoid collisions with Fedora's namespace."
                                    )
                            except ValueError:
                                # Complex Release format (e.g., with macros), skip validation
                                pass

    return True, None


def validate_package(package_name: str, check_actual_state: bool = True) -> tuple[bool, str | None]:
    """
    Validate modification_status for a package.

    Args:
        package_name: Package name to validate
        check_actual_state: If True, verify actual package state matches metadata
                          (requires cloning upstream, slow)

    Returns:
        (is_valid, error_message) tuple
    """
    metadata_file = METADATA_DIR / f'{package_name}.json'
    package_dir = RPMS_DIR / package_name

    # Check that both metadata and package dir exist
    if not metadata_file.exists():
        return False, f"{package_name}: Missing metadata file"

    if not package_dir.exists():
        return False, f"{package_name}: Missing package directory"

    # Load metadata
    with open(metadata_file) as f:
        metadata: PackageMetadata = json.load(f)

    # Check 1: Field must exist
    if 'modification_status' not in metadata:
        return False, f"{package_name}: Missing modification_status field"

    status = metadata['modification_status']

    # Check 2: Must be valid value
    if status not in ['clean', 'modified', 'native']:
        return False, f"{package_name}: Invalid modification_status '{status}'"

    # Check 3: Native packages should not have source/branch/sha fields
    if status == 'native':
        if 'source' in metadata or 'branch' in metadata or 'sha' in metadata:
            return False, f"{package_name}: Native package should not have source/branch/sha fields"
        # Native packages don't need further validation
        return True, None

    # Find the last Sync/Import commit once (used by multiple checks below)
    last_sync_sha = find_last_sync_commit(package_name)

    # Check 3.5: Local modifications must follow policy (%changelog and Release)
    is_valid, error = check_local_modifications(package_name, last_sync_sha)
    if not is_valid:
        return False, error

    # Check 4: track_upstream must be a string if present ("latest" or version prefix)
    if 'track_upstream' in metadata and not isinstance(metadata['track_upstream'], str):
        return False, f"{package_name}: track_upstream must be a string, got {type(metadata['track_upstream']).__name__}"

    # Check 4.5: release_monitoring_project_id must be an integer if present
    if 'release_monitoring_project_id' in metadata and not isinstance(metadata['release_monitoring_project_id'], int):
        return False, f"{package_name}: release_monitoring_project_id must be an integer"

    # Check 5: Modified packages must have reason
    if status == 'modified' and not metadata.get('modification_reason'):
        return False, f"{package_name}: Marked as modified but missing modification_reason"

    # Check 6: Verify git history matches metadata (fast check)
    # Skip if running expensive check (which uses filesystem comparison instead)
    if not check_actual_state and status in ['clean', 'modified']:
        is_clean_by_history, history_error = check_git_history_state(package_name, last_sync_sha)

        if status == 'clean' and not is_clean_by_history:
            return False, history_error

        if status == 'modified' and is_clean_by_history:
            return False, (
                f"{package_name}: Marked as modified but git history shows only "
                f"standard Import/Update/Sync commits. Consider marking as clean."
            )

    # Check 7: Verify actual state matches metadata (expensive check, optional)
    if check_actual_state and status in ['clean', 'modified']:
        with tempfile.TemporaryDirectory() as tmpdir:
            upstream_dir = Path(tmpdir) / 'upstream'

            logging.debug(f"{package_name}: Cloning {metadata['source']} at {metadata['branch']}...")
            run_git('clone', '--quiet', '--branch', metadata['branch'],
                   '--single-branch', metadata['source'], str(upstream_dir))

            logging.debug(f"{package_name}: Checking if modified...")
            actually_unmodified = is_package_unmodified(package_name, metadata, upstream_dir)

            if status == 'clean' and not actually_unmodified:
                return False, f"{package_name}: Marked as clean but package has modifications"

            if status == 'modified' and actually_unmodified:
                return False, f"{package_name}: Marked as modified but package is actually clean"

    return True, None


def find_packages_without_metadata() -> list[str]:
    """Find packages in rpms/ that don't have metadata files."""
    all_packages = {p.name for p in RPMS_DIR.iterdir() if p.is_dir()}
    packages_with_metadata = {p.stem for p in METADATA_DIR.glob('*.json')}
    return sorted(all_packages - packages_with_metadata)


def validate_packages(packages: list[str], check_actual_state: bool = True) -> int:
    """
    Validate multiple packages.

    Returns:
        Exit code (0 = success, 1 = validation failed)
    """
    errors = []
    total = len(packages)

    # Fast/history mode walks git log — requires a full clone.
    # Check once here, before spawning threads, so a misconfigured environment
    # fails immediately rather than after all workers have already started.
    if not check_actual_state:
        result = run_git('rev-parse', '--is-shallow-repository', cwd=ROOT_DIR, check=False)
        if result.stdout.strip() == 'true':
            logging.error("Cannot validate git history in shallow clone")
            sys.exit(1)

    # Use thread pool for parallel validation (git operations release GIL)
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all validation tasks
        future_to_pkg = {
            executor.submit(validate_package, pkg, check_actual_state): pkg
            for pkg in packages
        }

        # Collect results as they complete
        for i, future in enumerate(as_completed(future_to_pkg), 1):
            package_name = future_to_pkg[future]
            logging.info(f"[{i}/{total}] Validated {package_name}")

            valid, error = future.result()
            if not valid:
                errors.append(error)

    if errors:
        print("\n" + "=" * 60, file=sys.stderr)
        print("VALIDATION FAILED", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print("\n" + "=" * 60, file=sys.stderr)
        print(f"Total errors: {len(errors)}/{total}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 1

    print(f"\nValidation passed for all {total} package(s)")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Validate package modification_status metadata',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate packages changed in current MR (fast)
  %(prog)s --mr

  # Validate all packages (fast git history check)
  %(prog)s --all

  # Validate specific packages
  %(prog)s bash glibc gcc

  # Thorough validation with upstream clone check (slow)
  %(prog)s --all --thorough
"""
    )

    parser.add_argument('packages', nargs='*',
                       help='Package names to validate (default: all packages)')
    parser.add_argument('--mr', action='store_true',
                       help='Validate only packages changed in current MR')
    parser.add_argument('--all', action='store_true',
                       help='Validate all packages')
    parser.add_argument('--thorough', action='store_true',
                       help='Perform expensive upstream clone diff check instead of git history')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine which packages to validate
    if args.mr:
        packages = get_changed_packages_in_mr()
        if not packages:
            print("No packages changed in MR")
            return 0
        print(f"Validating {len(packages)} package(s) changed in MR:")
        for pkg in packages:
            print(f"  - {pkg}")
    elif args.all:
        # Check for packages missing metadata files
        missing_metadata = find_packages_without_metadata()
        if missing_metadata:
            print(f"\nERROR: Found {len(missing_metadata)} package(s) without metadata files:", file=sys.stderr)
            for pkg in missing_metadata:
                print(f"  - {pkg}", file=sys.stderr)
            return 1

        # Get all packages with metadata
        packages = sorted([p.stem for p in METADATA_DIR.glob('*.json')])
        print(f"Validating all {len(packages)} packages...")
    elif args.packages:
        packages = args.packages
        print(f"Validating {len(packages)} specified package(s)...")
    else:
        parser.error("Must specify --mr, --all, or package names")

    if args.thorough:
        print("Note: Checking actual package state (this may take a while)...")
    else:
        print("Note: Skipping actual state verification (fast mode)")

    return validate_packages(packages, args.thorough)


if __name__ == '__main__':
    sys.exit(main())
