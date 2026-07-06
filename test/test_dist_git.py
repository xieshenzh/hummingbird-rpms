"""Integration tests for dist_git importer."""

import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent

#
# Fixtures
#

@pytest.fixture
def dist_git_module():
    """Load the dist_git script as a Python module.

    For tests which need to mock internal functions.
    """
    script_path = Path(__file__).parent.parent / 'ci' / 'dist_git.py'
    module = types.ModuleType("dist_git")
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)
    return module


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Shallow copy of the project with no imports"""

    rpms_dir = tmp_path / 'workdir'
    rpms_dir.mkdir()

    subprocess.run(['git', 'init'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=rpms_dir, check=True)

    # Copy ci/, config, and templates
    shutil.copytree(project_root / 'ci', rpms_dir / 'ci')
    shutil.copy(project_root / 'target-packages.yml', rpms_dir / 'target-packages.yml')
    shutil.copytree(project_root / '.tekton', rpms_dir / '.tekton')
    shutil.copytree(project_root / 'konflux-templates', rpms_dir / 'konflux-templates')

    # Create directories for imports
    (rpms_dir / 'rpms').mkdir()
    (rpms_dir / 'metadata').mkdir()

    # Create default upstream-releases.json for tests
    # Note: rawhide is not included as it's auto-resolved to the highest version
    (rpms_dir / 'upstream-releases.json').write_text(
        json.dumps({'fedora': {'f40': 'f40', 'f99': 'f99'}}) + '\n'
    )

    subprocess.run(['git', 'add', '.'], cwd=rpms_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=rpms_dir, check=True)

    return rpms_dir


@pytest.fixture
def upstream_repos(tmp_path: Path) -> dict[str, Path]:
    """Create two mock upstream dist_git repositories."""
    repos: dict[str, Path] = {}

    # Create first upstream repo: vanilla
    vanilla_dir = tmp_path / 'upstream' / 'vanilla.git'
    vanilla_dir.mkdir(parents=True)
    subprocess.run(['git', 'init', '--initial-branch=rawhide'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=vanilla_dir, check=True)

    # Create a simple spec file
    (vanilla_dir / 'vanilla.spec').write_text("""Name: vanilla
Version: 1.0
Release: 1%{?dist}
Summary: Test package vanilla
License: MIT

%description
Test package

%files
""")
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=vanilla_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=vanilla_dir, check=True)
    repos['vanilla'] = vanilla_dir

    # Create second upstream repo: chocolate with a stable branch
    chocolate_dir = tmp_path / 'upstream' / 'chocolate.git'
    chocolate_dir.mkdir(parents=True)
    subprocess.run(['git', 'init', '--initial-branch=rawhide'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=chocolate_dir, check=True)

    choc_spec = chocolate_dir / 'chocolate.spec'
    choc_spec.write_text("""Name: chocolate
Version: 10
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=chocolate_dir, check=True)

    # Create f40 branch
    subprocess.run(['git', 'checkout', '-b', 'f40'], cwd=chocolate_dir, check=True)
    choc_spec.write_text("""Name: chocolate
Version: 4
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate (f40)

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Update for f40'], cwd=chocolate_dir, check=True)

    # Create f99 branch (higher version than f40, for testing release upgrades)
    subprocess.run(['git', 'checkout', '-b', 'f99'], cwd=chocolate_dir, check=True)
    choc_spec.write_text("""Name: chocolate
Version: 99
Release: 1%{?dist}
Summary: Test package chocolate
License: GPL

%description
Test package chocolate (f99)

%files
""")
    subprocess.run(['git', 'add', 'chocolate.spec'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Update for f99'], cwd=chocolate_dir, check=True)
    subprocess.run(['git', 'checkout', 'rawhide'], cwd=chocolate_dir, check=True)

    repos['chocolate'] = chocolate_dir

    return repos


#
# Helpers
#

def add_upstream_commit(repo_path: Path, package_name: str, old_version: str, new_version: str) -> str:
    """Add a commit to an upstream repository updating the version.

    Returns the new commit SHA.
    """
    spec_file = repo_path / f'{package_name}.spec'
    spec_file.write_text(spec_file.read_text().replace(f'Version: {old_version}', f'Version: {new_version}'))
    subprocess.run(['git', 'add', f'{package_name}.spec'], cwd=repo_path, check=True)
    subprocess.run(['git', 'commit', '-m', f'Update to {new_version}-1'], cwd=repo_path, check=True)
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_path,
                          text=True, stdout=subprocess.PIPE, check=True).stdout.strip()


def get_last_commit_info(workdir: Path) -> tuple[str, str]:
    """Get subject and body of the last commit.

    Returns (subject, body) tuple.
    """
    result = subprocess.run(['git', 'log', '-1', '--format=%s%n%n%b'], cwd=workdir,
                            text=True, stdout=subprocess.PIPE, check=True)
    lines = result.stdout.strip().split('\n')
    assert lines
    subject = lines[0]
    body = '\n'.join(lines[2:]) if len(lines) > 2 else ''
    return subject, body


def run_dist_git(dist_git_module, workdir: Path, *args: str) -> None:
    """Helper to run dist_git.main() in-process (when using mocks)."""
    # Set up module to use workdir
    dist_git_module.ROOT_DIR = workdir
    dist_git_module.RPMS_DIR = workdir / 'rpms'
    dist_git_module.METADATA_DIR = workdir / 'metadata'
    dist_git_module.RELEASES_JSON = workdir / 'upstream-releases.json'

    old_argv = sys.argv
    try:
        sys.argv = ['dist_git.py'] + list(args)
        dist_git_module.main()
    finally:
        sys.argv = old_argv


#
# Tests
#

def test_import(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Import a new package."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, check=True, text=True
    )

    assert "Successfully imported chocolate" in result.stderr

    chocolate_dir = workdir / 'rpms' / 'chocolate'
    assert (chocolate_dir / 'chocolate.spec').exists()
    assert not (chocolate_dir / '.git').exists()

    import_json_file = workdir / 'metadata' / 'chocolate.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['branch'] == 'rawhide'
    assert import_data['version'] == '10'
    assert import_data['release'] == '1'

    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-10-1'
    assert 'Branch: rawhide' in body
    assert f"Upstream: {import_data['sha']}" in body

    # did not miss any changes
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert status_result.stdout == '', f"git status should be clean after import, found:\n{status_result.stdout}"


def test_import_dry_run(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--dry-run prevents commits."""
    # Get initial commit (from fixture setup)
    initial_subject, _ = get_last_commit_info(workdir)

    # Import with --dry-run
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True, capture_output=True, text=True
    )

    assert "Successfully imported chocolate" in result.stderr

    # Verify files were imported
    chocolate_dir = workdir / 'rpms' / 'chocolate'
    assert (chocolate_dir / 'chocolate.spec').exists()

    # Verify metadata was created
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '10'

    # Verify no commit was created (still at initial commit)
    subject, _ = get_last_commit_info(workdir)
    assert subject == initial_subject


def test_import_sign_off(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--sign-off adds Signed-off-by trailer to commits."""
    # Import with --sign-off
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--sign-off', 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    assert "Successfully imported vanilla" in result.stderr

    # Verify the import worked
    import_json_file = workdir / 'metadata' / 'vanilla.json'
    assert import_json_file.exists()
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '1.0'

    # Verify commit has sign-off trailer
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import vanilla-1.0-1'
    assert f"Upstream: {import_data['sha']}" in body
    assert 'Signed-off-by: Test <test@example.com>' in body


def test_import_with_branch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Importing from a specific branch."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--branch', 'f40', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Check metadata has correct branch and version
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['branch'] == 'f40'
    assert import_data['version'] == '4'
    assert import_data['release'] == '1'

    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-4-1'
    assert 'Branch: f40' in body
    assert f"Upstream: {import_data['sha']}" in body


def test_import_existing_package_fails(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Importing an existing package fails."""
    # Create vanilla directory to simulate existing package
    (workdir / 'rpms' / 'vanilla').mkdir()

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert "ERROR: Package directory rpms/vanilla/ already exists" in result.stderr


def test_import_with_ref(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Import with --ref to get an older commit, then update moves to latest."""
    # Get initial SHA of chocolate
    old_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=upstream_repos["chocolate"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()

    # Add new commits to upstream
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')

    # Import using --ref to get the old version
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--ref', old_sha, f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Check that we imported the old version
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['sha'] == old_sha
    assert import_data['version'] == '10'
    assert import_data['branch'] == 'rawhide'

    # Verify commit message includes the old SHA
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Import chocolate-10-1'
    assert f"Upstream: {old_sha}" in body

    # Now run update to move to latest version
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Check that we're now at the latest version
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['sha'] == new_sha
    assert import_data['version'] == '12'

    # Verify update commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 10-1 to 12-1'
    assert f"Upstream: {new_sha}" in body


@pytest.mark.parametrize('release_line,expected_release', [
    ('Release: %autorelease', 'Release: 5%{?dist}\n'),
    ('Release:        %{autorelease}', 'Release:        5%{?dist}\n'),  # Preserves whitespace
    ('Release: %autorelease -p -s snapshot123', 'Release: 5%{?dist}\n'),  # Arguments consumed
])
def test_import_autorelease(workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
                            release_line: str, expected_release: str) -> None:
    """Test importing a package with %autorelease in Release line."""
    # Modify vanilla to use %autorelease
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Release: 1', release_line))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Mock get_mdapi_latest_build to return release "5"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '5.fc99'}

        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Verify %autorelease was replaced in spec file
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert expected_release in spec_content
    assert '%autorelease' not in spec_content

    # Verify metadata has correct release
    with open(workdir / 'metadata' / 'vanilla.json') as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['release'] == '5'


def test_update(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """update command"""
    # Case 1: Import vanilla (unmodified, current)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Case 2: Import chocolate (unmodified, will become outdated)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )
    # Get the sha that was imported
    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    with open(chocolate_import_json) as f:
        chocolate_import_data = json.load(f)
    initial_sha = chocolate_import_data['sha']

    # Update upstream chocolate
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Case 3: Create a modified package (strawberry) - import old chocolate, modify it, then chocolate gets updated
    strawberry_dir = workdir / 'rpms' / 'strawberry'
    strawberry_dir.mkdir()
    chocolate_spec = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec')
    # Copy chocolate spec to strawberry and modify it
    (strawberry_dir / 'chocolate.spec').write_text(chocolate_spec.read_text() + '\n# Local modification\n')
    # Add strawberry metadata (copy chocolate's old metadata, so it has an update available)
    strawberry_import_json = workdir / 'metadata' / 'strawberry.json'
    strawberry_import_data = chocolate_import_data.copy()
    strawberry_import_data['modification_status'] = 'modified'
    strawberry_import_data['modification_reason'] = 'Test local modification'
    with open(strawberry_import_json, 'w') as f:
        json.dump(strawberry_import_data, f, indent=2, sort_keys=True)
        f.write('\n')

    # Case 4: Downstream-only package (no metadata)
    mango_dir = workdir / 'rpms' / 'mango'
    mango_dir.mkdir()
    (mango_dir / 'mango.spec').write_text('Name: mango\nVersion: 1.0\nRelease: 1\n')

    # Let's not cover Koji check here
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Check results
    with open(chocolate_import_json) as f:
        chocolate_import_data = json.load(f)

    # Case 1: vanilla should be unchanged (already up-to-date)
    assert "already up-to-date" in result.stderr or "vanilla" in result.stderr

    # Case 2: chocolate should be updated to new version
    assert chocolate_import_data['sha'] != initial_sha, "chocolate should have been updated"
    assert chocolate_import_data['version'] == '11', "chocolate should be at version 11"
    assert "Updating chocolate" in result.stderr

    # Verify commits were created for chocolate and strawberry updates
    # strawberry is last (alphabetical order) so check it first (last commit)
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update strawberry from 10-1 to 11-1'

    # Case 3: strawberry should be updated with merge (has upstream update and local modifications)
    assert "Updating strawberry" in result.stderr
    assert "Local modifications applied successfully" in result.stderr

    # Case 4: mango should not be mentioned (no metadata)
    assert "mango" not in result.stderr


def test_update_uses_ls_remote(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """update uses ls-remote optimization for up-to-date packages."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Create a mock git wrapper that logs commands
    log_file = workdir / 'git_commands.log'
    bin_dir = workdir / 'bin'
    bin_dir.mkdir()
    mock_git = bin_dir / 'git'
    mock_git.write_text(f"""#!/bin/bash
echo "$@" >> {log_file}
exec /usr/bin/git "$@"
""")
    mock_git.chmod(0o755)

    # Update PATH to use mock git
    env = os.environ.copy()
    env['PATH'] = f"{bin_dir}:{env['PATH']}"

    # Run update with mock git
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True, env=env
    )

    assert "already up-to-date" in result.stderr

    # Should use ls-remote (not clone) to check if package is up-to-date
    git_commands = log_file.read_text().strip().split('\n')
    # Filter out config checks
    non_config_commands = [cmd for cmd in git_commands if not cmd.startswith('config ')]
    assert non_config_commands == ["ls-remote file://" + str(upstream_repos["vanilla"]) + " rawhide"]


def test_update_unbuilt(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update skips packages not built in Koji."""
    # Import chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream chocolate
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Mock Koji to return no build found (for both current and fallback)
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = None  # Build not found
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        # Should try fc99 first, then fallback to fc40 (both not found)
        assert mock_server.getBuild.call_count == 2
        mock_server.getBuild.assert_any_call('chocolate-11-1.fc99')
        mock_server.getBuild.assert_any_call('chocolate-11-1.fc40')
        chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
        with open(chocolate_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '10', "Should not update when build missing in Koji"


def test_update_built(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update proceeds when built in Koji."""
    # Import chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream chocolate
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Mock Koji to return a successful build with matching commit
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = {
            'build_id': 12345,
            'nvr': 'chocolate-11-1.fc99',
            'state': 1,  # COMPLETE
            'source': f'git+https://example.com/chocolate.git#{new_sha}'
        }
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        mock_server.getBuild.assert_called_once_with('chocolate-11-1.fc99')
        with open(workdir / 'metadata' / 'chocolate.json') as f:
            import_data = json.load(f)
        assert import_data['version'] == '11'
        assert import_data['sha'] == new_sha


def test_update_branch_dist_tag(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update uses correct dist tag for non-rawhide branches."""
    # Import chocolate from f40 branch
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--branch', 'f40', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Switch to f40 branch and update upstream chocolate
    subprocess.run(['git', 'checkout', 'f40'], cwd=upstream_repos["chocolate"], check=True)
    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '4', '5')

    # Mock Koji to return a successful build
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()
        mock_server.getBuild.return_value = {
            'build_id': 12345,
            'nvr': 'chocolate-5-1.fc40',
            'state': 1,  # COMPLETE
            'source': f'git+https://example.com/chocolate.git#{new_sha}'
        }
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'chocolate')
        # Should query for .fc40 (from branch f40), not rawhide version
        mock_server.getBuild.assert_called_once_with('chocolate-5-1.fc40')
        chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
        with open(chocolate_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '5'
        assert import_data['sha'] == new_sha


def test_update_rawhide_fallback(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Update finds rawhide build with previous release dist tag when current not found."""
    # Import vanilla from rawhide
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream vanilla
    new_sha = add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Mock Koji to:
    # 1. Return None for fc99 (current rawhide)
    # 2. Return successful build for fc40 (previous release fallback)
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        def getBuild_side_effect(nvr):
            if nvr == 'vanilla-2.0-1.fc99':
                return None  # Current rawhide not found
            elif nvr == 'vanilla-2.0-1.fc40':
                return {
                    'build_id': 12345,
                    'nvr': 'vanilla-2.0-1.fc40',
                    'state': 1,  # COMPLETE
                    'source': f'git+https://example.com/vanilla.git#{new_sha}'
                }
            return None

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Run update in-process
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla')

        # Should have called getBuild twice: once for fc99, then fallback to fc40
        assert mock_server.getBuild.call_count == 2
        mock_server.getBuild.assert_any_call('vanilla-2.0-1.fc99')
        mock_server.getBuild.assert_any_call('vanilla-2.0-1.fc40')

        # Package should be updated using the fallback
        vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
        with open(vanilla_import_json) as f:
            import_data = json.load(f)
        assert import_data['version'] == '2.0'
        assert import_data['sha'] == new_sha


def test_koji_retry_on_transient_error(dist_git_module) -> None:
    """Verify that 502 Bad Gateway errors are retried and eventually succeed."""
    import http.client

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # First two attempts fail with 502, third succeeds
        call_count = 0
        def getBuild_side_effect(nvr):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise http.client.HTTPException("502 Bad Gateway")
            return {
                'build_id': 12345,
                'nvr': 'test-1.0-1.fc40',
                'state': 1,
                'source': 'git://example.com/test#abc123',
            }

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Call should succeed after retries
        result = dist_git_module.call_koji_with_retry(
            mock_server.getBuild, 'test-1.0-1.fc40',
            method_name="Koji getBuild(test-1.0-1.fc40)"
        )

        assert result is not None
        assert result['build_id'] == 12345
        assert mock_server.getBuild.call_count == 3  # 2 failures + 1 success


def test_koji_no_retry_on_fault(dist_git_module) -> None:
    """Ensure XML-RPC Fault errors fail immediately without retries."""
    import xmlrpc.client

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # Raise Fault on every call
        mock_server.getBuild.side_effect = xmlrpc.client.Fault(1000, "Invalid build")
        mock_server_class.return_value = mock_server

        # Should raise immediately without retrying
        with pytest.raises(xmlrpc.client.Fault):
            dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

        # Should have called only once (no retries)
        assert mock_server.getBuild.call_count == 1


def test_koji_no_retry_on_auth_error(dist_git_module) -> None:
    """Ensure HTTP 401/403 errors fail immediately without retries."""
    import http.client

    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # Raise 401 Unauthorized
        mock_server.getBuild.side_effect = http.client.HTTPException("401 Unauthorized")
        mock_server_class.return_value = mock_server

        # Should raise immediately without retrying
        with pytest.raises(http.client.HTTPException):
            dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

        # Should have called only once (no retries)
        assert mock_server.getBuild.call_count == 1


def test_koji_retry_on_connection_error(dist_git_module) -> None:
    """Verify network connection errors are retried with exponential backoff."""
    with patch('xmlrpc.client.ServerProxy') as mock_server_class:
        mock_server = MagicMock()

        # First attempt fails with ConnectionError, second succeeds
        call_count = 0
        def getBuild_side_effect(nvr):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection reset by peer")
            return {
                'build_id': 12345,
                'nvr': 'test-1.0-1.fc40',
                'state': 1,
                'source': 'git://example.com/test#abc123',
            }

        mock_server.getBuild.side_effect = getBuild_side_effect
        mock_server_class.return_value = mock_server

        # Mock time.sleep to verify exponential backoff
        with patch('time.sleep') as mock_sleep:
            result = dist_git_module.call_koji_with_retry(
                mock_server.getBuild, 'test-1.0-1.fc40',
                method_name="Koji getBuild(test-1.0-1.fc40)"
            )

            assert result is not None
            assert result['build_id'] == 12345
            assert mock_server.getBuild.call_count == 2  # 1 failure + 1 success

            # Verify sleep was called with 2 seconds (first retry delay)
            mock_sleep.assert_called_once_with(2)


def test_sync(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync discards local modifications."""
    # Import chocolate (automatically commits)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True
    )

    # Make local modification, declare, and commit it
    choc_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    choc_spec.write_text(choc_spec.read_text() + '\n# Local modification\n')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', 'chocolate', '--modified',
         '--reason', 'Local test modification'],
        cwd=workdir, check=True,
    )

    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    new_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # sync succeeds and discards local modifications (automatically commits)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    choc_spec_content = choc_spec.read_text()
    assert '# Local modification' not in choc_spec_content

    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    with open(chocolate_import_json) as f:
        import_data = json.load(f)
    assert import_data['sha'] == new_sha
    assert import_data['version'] == '11'
    assert import_data['modification_status'] == 'clean'
    assert 'modification_reason' not in import_data

    # Verify sync commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Sync chocolate from 10-1 to 11-1'
    assert f"Upstream: {new_sha}" in body

    # Add another upstream commit
    new_sha2 = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')

    # Update should now pull in the new version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating chocolate" in result.stderr

    with open(chocolate_import_json) as f:
        import_data = json.load(f)
    assert import_data['sha'] == new_sha2
    assert import_data['version'] == '12'

    # Verify update commit was created
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 11-1 to 12-1'
    assert f"Upstream: {new_sha2}" in body


def test_sync_already_in_sync(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync fails when already at upstream."""
    # Import chocolate (automatically commits)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Sync without upstream changes fails
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'chocolate'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "already at upstream" in result.stderr


def test_sync_mark(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync --mark creates proper commit when already at upstream."""
    # Import chocolate (automatically commits)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    chocolate_import_json = workdir / 'metadata' / 'chocolate.json'
    original_metadata = chocolate_import_json.read_bytes()

    # Get the import commit sha to extract Upstream value
    subject, body = get_last_commit_info(workdir)
    import re
    upstream_match = re.search(r'Upstream: ([0-9a-f]{40})', body)
    assert upstream_match
    original_sha = upstream_match.group(1)

    # Sync --mark when already at upstream should succeed
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--mark', 'chocolate'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Verify sync commit was created even though no upstream changes
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Sync chocolate to 10-1 (mark)'
    assert f"Upstream: {original_sha}" in body

    # Metadata file should be bitwise identical
    assert chocolate_import_json.read_bytes() == original_metadata

    # Verify commit is empty (no actual changes)
    diff_result = subprocess.run(
        ['git', 'diff', 'HEAD~1', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True
    )
    assert diff_result.stdout == '', "Commit should be empty"


def test_sync_mark_resets_modification_status(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync --mark resets modification_status to clean and removes modification_reason."""
    # Import chocolate (automatically commits)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    chocolate_json = workdir / 'metadata' / 'chocolate.json'

    # Mark package as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'chocolate', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # Verify it's marked as modified
    metadata = json.loads(chocolate_json.read_text())
    assert metadata['modification_status'] == 'modified'
    assert metadata['modification_reason'] == 'Test modification'

    # Sync --mark should reset to clean
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--mark', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify modification_status is clean and modification_reason is removed
    metadata = json.loads(chocolate_json.read_text())
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata


def test_git_config_required(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Fails with helpful message if git user/email not configured."""

    subprocess.run(['git', 'config', '--unset', 'user.name'], cwd=workdir, check=True)
    subprocess.run(['git', 'config', '--unset', 'user.email'], cwd=workdir, check=True)
    env = os.environ.copy()
    env['GIT_CONFIG_GLOBAL'] = '/dev/null'
    env['GIT_CONFIG_SYSTEM'] = '/dev/null'

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, text=True, env=env
    )

    assert result.returncode != 0
    assert "Please configure git:" in result.stderr
    assert "git config user.name" in result.stderr

    # No changes were made (rpms/ should be unchanged)
    assert not (workdir / 'rpms' / 'chocolate').exists(), "Package should not be imported"

    # --dry-run doesn't require git config
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, capture_output=True, text=True, env=env, check=True
    )
    assert "Successfully imported chocolate" in result.stderr


def test_update_releases(workdir: Path, dist_git_module) -> None:
    """update-releases fetches from Bodhi and writes upstream-releases.json.

    Rawhide branch itself is not stored, but its dist_tag (e.g., f45) is captured.
    This handles the transition period when a new Fedora version hasn't branched
    yet and only exists as rawhide in Bodhi.
    """
    # mock curl response - rawhide's dist_tag should be captured
    mock_bodhi_response = {
        'releases': [
            {'id_prefix': 'FEDORA', 'branch': 'f41', 'dist_tag': 'f41'},
            {'id_prefix': 'FEDORA', 'branch': 'f43', 'dist_tag': 'f43'},
            {'id_prefix': 'FEDORA', 'branch': 'f44', 'dist_tag': 'f44'},
            {'id_prefix': 'FEDORA', 'branch': 'rawhide', 'dist_tag': 'f45'},
            {'id_prefix': 'FEDORA', 'branch': 'eln', 'dist_tag': 'eln'},
            {'id_prefix': 'FEDORA-EPEL', 'branch': 'epel9', 'dist_tag': 'epel9'},
        ]
    }

    with patch('subprocess.check_output') as mock_check_output:
        mock_check_output.return_value = json.dumps(mock_bodhi_response)

        # Run update-releases in-process
        run_dist_git(dist_git_module, workdir, 'update-releases')

    # Rawhide branch is NOT stored, but its dist_tag (f45) is captured
    assert json.loads((workdir / 'upstream-releases.json').read_text()) == {
        'centos': {
            'c9s': 'el9',
            'c10s': 'el10',
        },
        'fedora': {
            'eln': 'eln',
            'f41': 'f41',
            'f43': 'f43',
            'f44': 'f44',
            'f45': 'f45',  # rawhide's dist_tag is captured
            # EPEL should be filtered out (not FEDORA id_prefix)
        },
    }


def test_get_dist_tag_rawhide_auto_resolution(dist_git_module) -> None:
    """get_dist_tag automatically resolves rawhide to the highest Fedora release."""
    # Set up releases with various Fedora versions (rawhide not in the dict)
    dist_git_module.releases = {
        'fedora': {
            'f40': 'f40',
            'f42': 'f42',
            'f43': 'f43',
            'eln': 'eln',
        },
    }

    # rawhide should resolve to f43 (highest numbered release)
    assert dist_git_module.get_dist_tag('rawhide') == 'f43'

    # Other branches should work normally
    assert dist_git_module.get_dist_tag('f40') == 'f40'
    assert dist_git_module.get_dist_tag('f42') == 'f42'
    assert dist_git_module.get_dist_tag('eln') == 'eln'

    # CentOS Stream branches should still work
    assert dist_git_module.get_dist_tag('c9s') == 'el9'
    assert dist_git_module.get_dist_tag('c10s') == 'el10'


def test_expand_url_shortcut(dist_git_module) -> None:
    """expand_url_shortcut resolves fedora/ and centos/ shortcuts."""
    # Fedora shortcut
    assert dist_git_module.expand_url_shortcut('fedora/bash') == \
        'https://src.fedoraproject.org/rpms/bash.git'

    # CentOS shortcut
    assert dist_git_module.expand_url_shortcut('centos/kernel') == \
        'https://gitlab.com/redhat/centos-stream/rpms/kernel.git'

    # Full URL passes through unchanged
    full_url = 'https://gitlab.com/redhat/centos-stream/rpms/golang.git'
    assert dist_git_module.expand_url_shortcut(full_url) == full_url


def test_update_of_rebuild(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update proceeds when local changes only affect Release: field.

    This commonly happens for rebuilds.
    """
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify the Release: field locally (bump it for a rebuild)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Release: 1', 'Release: 2')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Rebuild because reasons'], cwd=workdir, check=True)

    # Add upstream commit with new version
    new_sha = add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Update should proceed despite local Release: modification
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Updates successfully
    assert "Updating vanilla" in result.stderr
    assert "local modifications" not in result.stderr
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['sha'] == new_sha
    assert import_data['version'] == '2.0'
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update vanilla from 1.0-1 to 2.0-1'
    assert f"Upstream: {new_sha}" in body

def test_update_commit_uses_local_package_name(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update commit message uses local directory name, not upstream repo name.

    When a package is imported with --directory (e.g. golang imported as golang1.26),
    the commit message must use the local name so that CI scripts can find the correct
    rpms/ directory for conflict detection.
    """
    # Import vanilla with a different directory name (simulates golang -> golang1.26)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--directory', 'vanilla1.0',
         f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Add upstream commit (Release bump only — version stays in 1.0 series)
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated = upstream_content.replace('Release: 1', 'Release: 2')
    upstream_spec.write_text(updated)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump release'],
                   cwd=upstream_repos['vanilla'], check=True)

    # Update
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla1.0'],
        cwd=workdir, check=True,
    )

    # Commit message must use local name "vanilla1.0", not upstream "vanilla"
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Update vanilla1.0 from 1.0-1 to 1.0-2'


def test_mark_modified_with_reason(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Mark a clean package as modified with a reason."""
    # Import vanilla package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Verify it starts as clean
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata

    # Mark as modified with a reason
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Backport CVE fix from upstream'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Verify metadata updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'modified'
    assert metadata['modification_reason'] == 'Backport CVE fix from upstream'


def test_mark_clean(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Mark a modified package back to clean."""
    # Import vanilla package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'modified'
    assert 'modification_reason' in metadata

    # Mark back to clean
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--clean'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Verify metadata updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert 'modification_reason' not in metadata


@pytest.mark.parametrize("modify_release", [False, True], ids=["clean", "with_release"])
def test_update_merge_clean(workdir: Path, upstream_repos: dict[str, Path], modify_release: bool) -> None:
    """Update automatically merges non-conflicting local and upstream changes.

    Tests both:
    - Clean merge without local Release: changes
    - Merge with local Release: bump that is normalized out
    """
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Make local modification: add comment and optionally bump Release
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_spec = spec_content.replace('%description', '# Local comment\n%description')
    if modify_release:
        modified_spec = modified_spec.replace('Release: 1', 'Release: 1.1')

    vanilla_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local comment added', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make upstream change: bump Release and add different comment
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('Release: 1', 'Release: 2')
    updated_upstream = updated_upstream.replace('%files', '# Upstream comment\n%files')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump release and add comment'],
                   cwd=upstream_repos['vanilla'], check=True)

    # update automatically merges
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Merge failed: {result.stderr}"

    # Verify both local and upstream comments are present, upstream Release wins
    merged_spec = vanilla_spec.read_text()
    assert '# Local comment' in merged_spec, "Local modification missing"
    assert '# Upstream comment' in merged_spec, "Upstream change missing"
    assert 'Release: 2' in merged_spec, "Should use upstream Release"
    if modify_release:
        assert 'Release: 1.1' not in merged_spec, "Local Release bump should not persist"

    # Verify commit message
    subject, body = get_last_commit_info(workdir)
    assert subject.startswith('Update vanilla from')
    assert 'Upstream:' in body

    # Verify modification_status is still 'modified'
    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    assert metadata['modification_status'] == 'modified'


def test_update_merge_conflict(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update leaves conflicts in working tree for manual resolution."""
    # Import chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Make local modification: change License line
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    modified_spec = spec_content.replace('License: GPL', 'License: MIT')
    chocolate_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make conflicting upstream change: change the same License line differently and bump version
    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('License: GPL', 'License: Apache-2.0')
    updated_upstream = updated_upstream.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    # update exits with code 2 (conflicts)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2, f"Expected exit code 2 for conflicts, got {result.returncode}"
    assert 'conflict' in result.stderr.lower(), "Expected 'conflict' in stderr"
    assert 'update --continue' in result.stderr, "Expected --continue instructions"

    # Verify state file was written for --continue
    state_file = workdir / '.dist_git_update_state.json'
    assert state_file.exists(), "Expected state file for --continue"
    with open(state_file) as f:
        state = json.load(f)
    assert state['package'] == 'chocolate'
    assert 'Update chocolate' in state['commit_msg']
    assert 'metadata' in state, "State file should contain updated metadata for --continue"
    assert state['metadata']['version'] == '11'

    # Verify NO new commit was created (conflicts left in working tree)
    commits_after = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    # Initial commit, Import, Local modification — no Update commit
    assert commits_after == "3"

    # Verify metadata was NOT staged (HUM-2672: prevents leak into subsequent commits)
    metadata_status = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert 'metadata/chocolate.json' not in metadata_status

    # Verify metadata on disk was not modified (HUM-2672: never written on conflict path)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        disk_metadata = json.load(f)
    assert disk_metadata['version'] == '10', "Metadata on disk should remain at pre-update version"

    # Verify spec file has conflict markers in working tree
    spec_content = chocolate_spec.read_text()
    assert '<<<<<<< HEAD' in spec_content
    assert '>>>>>>> hummingbird-local' in spec_content
    assert 'License: MIT' in spec_content
    assert 'License: Apache-2.0' in spec_content


def test_multi_mr_commits_conflicted_update_for_dry_run_mr(
    workdir: Path, upstream_repos: dict[str, Path]
) -> None:
    """Multi-MR wrapper turns dist_git exit 2 into a conflict MR candidate."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    chocolate_spec.write_text(chocolate_spec.read_text().replace('License: GPL', 'License: MIT'))
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    updated_upstream = upstream_spec.read_text().replace('License: GPL', 'License: Apache-2.0')
    updated_upstream = updated_upstream.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    branch = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=workdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    env = os.environ.copy()
    env['CI_COMMIT_BRANCH'] = branch
    env['GITLAB_REMOTE_URL'] = f'file://{workdir}'
    env['DIST_GIT_UPDATE_SKIP_BUILD_CHECK'] = 'true'

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git_update_multi_mr.sh'),
         '--clone', '--only-package=chocolate'],
        cwd=workdir, capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Update found (CONFLICTS - needs manual resolution)' in result.stdout
    assert 'Type: CONFLICT (no auto-merge)' in result.stdout
    assert 'Updates failed:          0' in result.stdout


def test_update_merge_version_bump(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update handles modified packages where the local change is a Version bump.

    When the local modification changes Version (adjacent to Release in the spec),
    the Release normalization rebase can fail due to diff hunk context overlap.
    The merge should fall back to applying raw local modifications.
    """
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Make local modification: bump Version (adjacent to Release — triggers rebase conflict)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_spec = spec_content.replace('Version: 1.0', 'Version: 1.5')
    modified_spec = modified_spec.replace('Release: 1', 'Release: 0.1')
    vanilla_spec.write_text(modified_spec)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Version bump to 1.5', 'vanilla'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make upstream change: different Version bump + add a comment
    upstream_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    upstream_content = upstream_spec.read_text()
    updated_upstream = upstream_content.replace('Version: 1.0', 'Version: 2.0')
    updated_upstream = updated_upstream.replace('Release: 1', 'Release: 2')
    updated_upstream = updated_upstream.replace('%files', '# Upstream comment\n%files')
    upstream_spec.write_text(updated_upstream)
    subprocess.run(['git', 'commit', '-a', '-m', 'Upstream version bump'],
                   cwd=upstream_repos['vanilla'], check=True)

    # update should succeed (with conflicts on Version, but not crash)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode in (0, 2), f"Update crashed: {result.stderr}"

    # Verify upstream content was merged into the result
    merged_spec = vanilla_spec.read_text()
    assert '# Upstream comment' in merged_spec, "Upstream change should be merged in"
    assert 'Version: 2.0' in merged_spec or '<<<<<<< HEAD' in merged_spec, \
        "Upstream version or conflict markers should be present"


def test_update_skips_package_without_upstream_metadata(workdir: Path) -> None:
    """Packages without source/branch/sha are skipped instead of crashing."""
    package_dir = workdir / 'rpms' / 'signed-stub'
    package_dir.mkdir()
    (package_dir / 'signed-stub.spec').write_text("""Name: signed-stub
Version: 1
Release: 1
Summary: Signed stub
License: MIT

%description
Signed stub

%files
""")
    (workdir / 'metadata' / 'signed-stub.json').write_text(json.dumps({
        'version': '1',
        'release': '1',
        'modification_status': 'modified',
        'modification_reason': 'trigger rebuild to sign the RPMs',
    }, indent=2) + '\n')
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add signed stub'], cwd=workdir, check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'signed-stub'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode == 0
    assert 'Skipping signed-stub: no Fedora upstream configured' in result.stderr


def test_update_skips_upstream_without_spec(
    workdir: Path, upstream_repos: dict[str, Path]
) -> None:
    """Retired/no-spec upstream branches are skipped instead of failing all updates."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    vanilla_spec.unlink()
    (upstream_repos['vanilla'] / 'dead.package').write_text('Retired package\n')
    subprocess.run(['git', 'add', '-A'], cwd=upstream_repos['vanilla'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Retire package'], cwd=upstream_repos['vanilla'], check=True)

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode == 0
    assert 'Skipping vanilla: upstream branch rawhide is retired' in result.stderr
    metadata = json.loads((workdir / 'metadata' / 'vanilla.json').read_text())
    assert metadata['version'] == '1.0'


def test_native_package_blocks_update(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Verify that native packages block automatic updates."""
    # Create a native package manually
    native_dir = workdir / 'rpms' / 'native-pkg'
    native_dir.mkdir()
    (native_dir / 'native-pkg.spec').write_text("""Name: native-pkg
Version: 1.0
Release: 1
Summary: Native package
License: MIT

%description
Native package

%files
""")

    # Create metadata for native package
    metadata_file = workdir / 'metadata' / 'native-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'release': '1',
            'modification_status': 'native',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add native package'], cwd=workdir, check=True)

    # Update should fail
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'native-pkg'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should exit with error
    assert result.returncode != 0
    assert "Cannot update native package native-pkg" in result.stderr


def test_list_all_packages(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test list command shows all packages."""
    # Import vanilla package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Create a native package
    native_dir = workdir / 'rpms' / 'native-pkg'
    native_dir.mkdir()
    (native_dir / 'native-pkg.spec').write_text("""Name: native-pkg
Version: 1.0
Release: 1
Summary: Native package
License: MIT

%description
Native package

%files
""")

    metadata_file = workdir / 'metadata' / 'native-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'release': '1',
            'modification_status': 'native',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add native package'], cwd=workdir, check=True)

    # List all packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show both packages
    assert 'ALL PACKAGES (2)' in result.stdout
    assert 'vanilla' in result.stdout
    assert 'native-pkg' in result.stdout
    assert '[clean]' in result.stdout
    assert '[native]' in result.stdout


def test_list_modified_only(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test list --modified shows only modified packages."""
    # Import vanilla package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # List modified packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--modified'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show modified package with reason
    assert 'MODIFIED PACKAGES (1)' in result.stdout
    assert 'vanilla' in result.stdout
    assert '[modified]' in result.stdout
    assert 'Test modification' in result.stdout

    # --name-only output
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--modified', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only package name without formatting
    assert result.stdout == 'vanilla\n'


def test_list_clean_only(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test list --clean shows only clean packages."""
    # Import vanilla and chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Mark vanilla as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test'],
        cwd=workdir, check=True,
    )

    # List clean packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--clean'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only chocolate
    assert 'CLEAN PACKAGES (1)' in result.stdout
    assert 'chocolate' in result.stdout
    assert 'vanilla' not in result.stdout


def test_list_native_only(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test list --native shows only native packages."""
    # Import vanilla package (clean)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Create a native package
    native_dir = workdir / 'rpms' / 'native-pkg'
    native_dir.mkdir()
    (native_dir / 'native-pkg.spec').write_text("""Name: native-pkg
Version: 1.0
Release: 1
Summary: Native package
License: MIT

%description
Native package

%files
""")

    metadata_file = workdir / 'metadata' / 'native-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'release': '1',
            'modification_status': 'native',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add native package'], cwd=workdir, check=True)

    # List native packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--native'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show only native package
    assert 'NATIVE PACKAGES (1)' in result.stdout
    assert 'native-pkg' in result.stdout
    assert '[native]' in result.stdout
    assert 'vanilla' not in result.stdout


def test_diff_modified_package(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test diff shows changes for modified package."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify spec file
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'], cwd=workdir, check=True)

    # Mark as modified
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test modification'],
        cwd=workdir, check=True,
    )

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show the modification
    assert result.returncode == 1  # Exit code 1 means diffs found
    assert 'Version: 1.0' in result.stdout
    assert 'Version: 1.1' in result.stdout


def test_diff_clean_package(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test diff shows nothing for clean package."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should show nothing and exit with 0
    assert result.returncode == 0
    assert result.stdout.strip() == ''


def test_diff_native_package(workdir: Path) -> None:
    """Test diff skips native packages with message."""
    # Create a native package
    native_dir = workdir / 'rpms' / 'native-pkg'
    native_dir.mkdir()
    (native_dir / 'native-pkg.spec').write_text("""Name: native-pkg
Version: 1.0
Release: 1
Summary: Native package
License: MIT

%description
Native package

%files
""")

    metadata_file = workdir / 'metadata' / 'native-pkg.json'
    with open(metadata_file, 'w') as f:
        json.dump({
            'version': '1.0',
            'release': '1',
            'modification_status': 'native',
        }, f, indent=2)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add native package'], cwd=workdir, check=True)

    # Run diff command
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'native-pkg'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should skip with message
    assert result.returncode == 0
    assert 'Native package (no upstream to diff against)' in result.stdout


def test_diff_raw_mode(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test --raw mode shows Release: changes."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Bump Release only
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Release: 1', 'Release: 2')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Rebuild'], cwd=workdir, check=True)

    # Run diff without --raw (should show nothing)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ''

    # Run diff with --raw (should show Release change)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla', '--raw'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert 'Release: 1' in result.stdout
    assert 'Release: 2' in result.stdout


def test_diff_name_only_mode(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test --name-only mode shows only filenames."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify spec file
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump version'], cwd=workdir, check=True)

    # Run diff --name-only
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', 'vanilla', '--name-only'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show only filename
    assert result.returncode == 1
    assert 'vanilla.spec' in result.stdout
    # Should NOT show diff content
    assert 'Version:' not in result.stdout or 'differ' in result.stdout


def test_diff_all_modified(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test --all diffs all modified packages."""
    # Import multiple packages
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Mark vanilla as modified and modify it
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'vanilla', '--modified',
         '--reason', 'Test'],
        cwd=workdir, check=True,
    )
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 1.1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump vanilla version'], cwd=workdir, check=True)

    # Mark chocolate as modified and modify it
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', 'chocolate', '--modified',
         '--reason', 'Test2'],
        cwd=workdir, check=True,
    )
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    modified_content = spec_content.replace('Version: 10', 'Version: 11')
    chocolate_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump chocolate version'], cwd=workdir, check=True)

    # Run diff --all
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'diff', '--all'],
        cwd=workdir, capture_output=True, text=True,
    )

    # Should show both modified packages
    assert result.returncode == 1
    assert '=== vanilla ===' in result.stdout
    assert '=== chocolate ===' in result.stdout
    assert 'Version: 1.1' in result.stdout
    assert 'Version: 11' in result.stdout


def test_is_prerelease_tilde_notation(dist_git_module) -> None:
    """Test pre-release detection for tilde notation."""
    assert dist_git_module.is_prerelease("5.3.0~rc1") == (True, "tilde pre-release marker (~rc1)")
    assert dist_git_module.is_prerelease("2.0~beta1")[0] is True
    assert dist_git_module.is_prerelease("1.0~alpha")[0] is True
    assert dist_git_module.is_prerelease("3.0~pre")[0] is True


def test_is_prerelease_suffix_notation(dist_git_module) -> None:
    """Test pre-release detection for suffix notation."""
    assert dist_git_module.is_prerelease("5.3.0-rc1")[0] is True
    assert dist_git_module.is_prerelease("2.0.beta1")[0] is True
    assert dist_git_module.is_prerelease("1.0-alpha")[0] is True
    assert dist_git_module.is_prerelease("3.0.dev")[0] is True


def test_is_prerelease_stable_versions(dist_git_module) -> None:
    """Test that stable versions are not flagged as pre-release."""
    assert dist_git_module.is_prerelease("1.0") == (False, None)
    assert dist_git_module.is_prerelease("2.5.3") == (False, None)
    assert dist_git_module.is_prerelease("10.11.12") == (False, None)
    # Edge case: package with "dev" in name shouldn't match
    assert dist_git_module.is_prerelease("1.0device") == (False, None)


def test_is_prerelease_in_release_field(dist_git_module) -> None:
    """Test pre-release detection in release field (e.g., kernel-headers pattern)."""
    # Kernel-style pre-release in release field (7.0.0-0.rc1.15)
    is_pre, pattern = dist_git_module.is_prerelease("7.0.0", "0.rc1.15")
    assert is_pre is True
    assert "rc1" in pattern
    assert "in release" in pattern

    # Other release field pre-release patterns
    assert dist_git_module.is_prerelease("2.5.0", "0.beta1")[0] is True
    assert dist_git_module.is_prerelease("3.0.0", "0.1.alpha")[0] is True
    assert dist_git_module.is_prerelease("1.0.0", "0.rc2.5")[0] is True

    # Stable version with stable release
    assert dist_git_module.is_prerelease("1.0", "1") == (False, None)
    assert dist_git_module.is_prerelease("2.5.3", "59") == (False, None)

    # Pre-release in version should still be detected
    is_pre, pattern = dist_git_module.is_prerelease("5.3.0~rc1", "1")
    assert is_pre is True
    assert "in release" not in pattern  # Should be detected in version, not release


def test_update_skips_prerelease(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update skips pre-release versions by default."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update should skip
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "pre-release version detected" in result.stderr
    assert "2.0~rc1" in result.stderr

    # Verify package not updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '1.0'


def test_update_allow_prerelease_flag(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --allow-prerelease accepts pre-release versions."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update with --allow-prerelease should proceed
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check',
         '--allow-prerelease', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating vanilla" in result.stderr

    # Verify package updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '2.0~rc1'


def test_update_batch_continues_on_prerelease(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Batch update continues processing after encountering pre-release."""
    # Import vanilla and chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update vanilla to pre-release
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update chocolate to stable version
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Batch update should skip vanilla but update chocolate
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert "Skipping vanilla" in result.stderr or "pre-release" in result.stderr
    assert "Updating chocolate" in result.stderr
    # Exit code may be non-zero if updates occurred, that's ok
    assert result.returncode in (0, 1)


@pytest.mark.parametrize('use_indirect', [False, True], ids=['direct', 'indirect'])
def test_update_autorelease(workdir: Path, upstream_repos: dict[str, Path], dist_git_module,
                            use_indirect: bool) -> None:
    """Test updating a package with %autorelease (direct or indirect)."""
    # Modify vanilla to use %autorelease (direct or indirect via macro)
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace(
        'Release: 1',
        '%global my_release %{autorelease}\nRelease: %{my_release}' if use_indirect
        else 'Release: %autorelease'
    ))

    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Import with release "2"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Make a minor upstream change (same version)
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Summary: Test package vanilla', 'Summary: Test package update'))
    subprocess.run(['git', 'commit', '-am', 'Update summary'], cwd=upstream_repos['vanilla'], check=True)

    # Update with incremented release "3" (same version)
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '3.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify that update: same version, incremented release
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 1.0' in spec_content
    assert 'Summary: Test package update' in spec_content
    assert '%autorelease' not in spec_content

    if use_indirect:
        # Indirect: macro definition should have the release, Release: line stays as %{my_release}
        assert '%global my_release 3%{?dist}' in spec_content
        assert 'Release: %{my_release}' in spec_content
    else:
        # Direct: Release line should be updated
        assert 'Release: 3%{?dist}\n' in spec_content

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['release'] == '3'

    # Update upstream to version 2.0, this usually resets release to 1 in MDAPI
    upstream_spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(upstream_spec_content.replace('Version: 1.0', 'Version: 2.0'))
    subprocess.run(['git', 'commit', '-am', 'Update to 2.0'], cwd=upstream_repos['vanilla'], check=True)

    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '2.0', 'release': '1.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify second update: new version, reset release
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 2.0' in spec_content
    assert '%autorelease' not in spec_content

    if use_indirect:
        # Indirect: macro definition should have reset release
        assert '%global my_release 1%{?dist}' in spec_content
        assert 'Release: %{my_release}' in spec_content
    else:
        # Direct: Release line should be reset
        assert 'Release: 1%{?dist}\n' in spec_content

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'
    assert metadata['release'] == '1'


def test_update_autorelease_modified(workdir: Path, upstream_repos: dict[str, Path], dist_git_module) -> None:
    """Test updating a modified package with %autorelease (triggers merge path)."""
    # Modify vanilla to use %autorelease
    vanilla_spec = upstream_repos['vanilla'] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Release: 1', 'Release: %autorelease'))
    subprocess.run(['git', 'commit', '-am', 'Use autorelease'], cwd=upstream_repos['vanilla'], check=True)

    # Import with release "2"
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '2.fc99'}
        run_dist_git(dist_git_module, workdir, 'import', f'file://{upstream_repos["vanilla"]}')

    # Make a local modification (to trigger merge path)
    local_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    local_content = local_spec.read_text()
    local_spec.write_text(local_content.replace('License: MIT', 'License: MIT\n# Local comment'))
    run_dist_git(dist_git_module, workdir, 'mark-modified', 'vanilla', '--modified',
                 '--reason', 'Test local modification')
    subprocess.run(['git', 'commit', '-am', 'Local modification'], cwd=workdir, check=True)

    # Make a minor upstream change (same version, still uses %autorelease)
    spec_content = vanilla_spec.read_text()
    vanilla_spec.write_text(spec_content.replace('Summary: Test package vanilla', 'Summary: Test package update'))
    subprocess.run(['git', 'commit', '-am', 'Update summary'], cwd=upstream_repos['vanilla'], check=True)

    # Update with incremented release "3" (should merge local + upstream changes)
    with patch.object(dist_git_module, 'get_mdapi_latest_build') as mock_mdapi:
        mock_mdapi.return_value = {'version': '1.0', 'release': '3.fc99'}
        run_dist_git(dist_git_module, workdir, 'update', 'vanilla', '--skip-build-check')

    # Verify that update: same version, incremented release, both changes merged
    spec_content = (workdir / 'rpms' / 'vanilla' / 'vanilla.spec').read_text()
    assert 'Version: 1.0' in spec_content
    assert 'Release: 3%{?dist}\n' in spec_content
    assert 'Summary: Test package update' in spec_content  # Upstream change
    assert '# Local comment' in spec_content  # Local modification preserved
    assert '%autorelease' not in spec_content

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'
    assert metadata['release'] == '3'


def test_replace_autorelease_optional_when_local_merge_removed_macro(
    tmp_path: Path, dist_git_module
) -> None:
    """Update merge path tolerates local specs that already replaced %autorelease."""
    package_dir = tmp_path / 'package'
    package_dir.mkdir()
    spec_file = package_dir / 'package.spec'
    spec_file.write_text("""Name: package
Version: 1.0
Release: 0.1%{?dist}
Summary: Test package
License: MIT
""")

    dist_git_module.replace_autorelease_in_spec(package_dir, '3', required=False)

    assert spec_file.read_text().count('Release: 0.1%{?dist}') == 1


def test_update_with_ref(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --ref to get a specific older commit."""
    # Import chocolate at latest
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Get the current SHA
    import_json_file = workdir / 'metadata' / 'chocolate.json'
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['version'] == '10'

    # Add new commits upstream
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')
    intermediate_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '11', '12')
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '12', '13')

    # Update with --ref to get the intermediate version (not latest)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--ref', intermediate_sha,
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Check that we updated to the intermediate version
    with open(import_json_file) as f:
        import_data = json.load(f)
    assert import_data['sha'] == intermediate_sha
    assert import_data['version'] == '12'
    assert import_data['release'] == '1'

    # Verify commit message includes the intermediate SHA
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Update chocolate from 10-1 to 12-1'
    assert f"Upstream: {intermediate_sha}" in body

    # Spec should have version 12
    spec_content = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec').read_text()
    assert 'Version: 12' in spec_content


def test_sync_bypasses_prerelease_check(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync command bypasses pre-release check."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Update upstream to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Sync should update despite pre-release version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Syncing vanilla" in result.stderr

    # Verify package updated
    vanilla_import_json = workdir / 'metadata' / 'vanilla.json'
    with open(vanilla_import_json) as f:
        import_data = json.load(f)
    assert import_data['version'] == '2.0~rc1'


def test_set_upstream_track(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --track-version latest sets track_upstream to 'latest'."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == 'latest'


def test_set_upstream_no_track(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-track-version removes track_upstream from metadata."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Enable first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == 'latest'

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--no-track-version'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata


def test_set_upstream_project_id_string(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --project-id with string sets upstream name in metadata."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--project-id', 'golang'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 'golang'


def test_set_upstream_track_version(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --track-version stores version prefix in track_upstream."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'


def test_set_upstream_no_track_version(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-track-version removes track_upstream."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Set track_version first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla', '--no-track-version'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert 'track_upstream' not in metadata


def test_set_upstream_project_id(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --project-id sets release_monitoring_project_id."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--project-id', '13254'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 13254


def test_set_upstream_no_project_id(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream --no-project-id removes release_monitoring_project_id."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Set project ID and tracking first
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', 'latest', '--project-id', '13254'],
        cwd=workdir, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['release_monitoring_project_id'] == 13254

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla', '--no-project-id'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == 'latest'
    assert 'release_monitoring_project_id' not in metadata


def test_set_upstream_combined(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream with multiple flags sets all specified fields."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.26', '--project-id', '13254'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['track_upstream'] == '1.26'
    assert metadata['release_monitoring_project_id'] == 13254


def test_list_prerelease_packages(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test list --prerelease shows only packages with pre-release versions."""
    # Import vanilla and chocolate
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update vanilla to pre-release version
    vanilla_spec = upstream_repos["vanilla"] / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace('Version: 1.0', 'Version: 2.0~rc1')
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'add', 'vanilla.spec'], cwd=upstream_repos["vanilla"], check=True)
    subprocess.run(['git', 'commit', '-m', 'Update to 2.0~rc1'], cwd=upstream_repos["vanilla"], check=True)

    # Update vanilla package to pre-release
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, check=True,
    )

    # List pre-release packages
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'list', '--prerelease'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    # Should only show vanilla, not chocolate
    assert 'vanilla' in result.stdout
    assert '2.0~rc1' in result.stdout
    assert 'chocolate' not in result.stdout
    assert 'PRE-RELEASE PACKAGES (1):' in result.stdout


def test_set_upstream_unknown_package(workdir: Path) -> None:
    """set-upstream on non-existent package fails."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'nonexistent',
         '--track-version', 'latest'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "Package nonexistent not found" in result.stderr


def test_set_upstream_no_flags(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """set-upstream with no flags exits with error."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla'],
        cwd=workdir, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "set-upstream requires at least one flag" in result.stderr


def test_update_skips_when_track_version_mismatch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update skips package when upstream version doesn't match track_version prefix."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Set track_version to 1.0 (matches current version)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1.0'],
        cwd=workdir, check=True,
    )

    # Update upstream to version 2.0 (doesn't match track_version 1.0)
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')

    # Update should skip due to track_version mismatch
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "doesn't match tracked version" in result.stderr

    # Verify package was NOT updated
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.0'


def test_update_proceeds_when_track_version_matches(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update proceeds when upstream version matches track_version prefix."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Set track_version to 1 (matches 1.0 and 1.x)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'set-upstream', 'vanilla',
         '--track-version', '1'],
        cwd=workdir, check=True,
    )

    # Update upstream to version 1.5 (matches track_version prefix "1")
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '1.5')

    # Update should proceed
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Updating vanilla" in result.stderr

    # Verify package was updated
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.5'


def test_update_skips_older_version(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update skips when upstream version is older than current version."""
    # Import vanilla at 1.0
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Bump local version to 2.0 ahead of upstream
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, check=True,
    )
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'

    # Now upstream goes back to 1.5 (simulating a branch reset or different branch state)
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '2.0', '1.5')

    # Update should skip due to older version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "upstream version 1.5 is older than current 2.0" in result.stderr

    # Verify package was NOT updated
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '2.0'


def test_sync_allows_older_version(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync allows downgrade to older upstream version."""
    # Import vanilla at 1.0
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Bump local version to 2.0
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '1.0', '2.0')
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'vanilla'],
        cwd=workdir, check=True,
    )

    # Now upstream goes back to 1.5
    add_upstream_commit(upstream_repos["vanilla"], 'vanilla', '2.0', '1.5')

    # Sync should proceed despite older version
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', 'vanilla'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )

    assert "Syncing vanilla" in result.stderr

    # Verify package WAS updated (downgraded)
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['version'] == '1.5'


def test_rebuild(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild command bumps Release field correctly."""
    # Import vanilla package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Rebuild the package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    assert 'Release: 1.1%{?dist}' in spec_content

    # Verify commit message
    subject, body = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: test rebuild'

    # Verify git status is clean
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert status_result.stdout == ''

    # Verify metadata was NOT changed (rebuilds don't affect modification_status)
    metadata_file = workdir / 'metadata' / 'vanilla.json'
    with open(metadata_file) as f:
        metadata = json.load(f)
    assert metadata['modification_status'] == 'clean'
    assert metadata['version'] == '1.0'
    assert metadata['release'] == '1'


def test_rebuild_already_bumped(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild increments existing .N suffix."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify to have Release: 2.1%{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    # After import, the file has %{?dist} added, so replace the whole line
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 2.1%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Bump to 2.1'], cwd=workdir, check=True)

    # Rebuild again
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped to 2.2
    spec_content = vanilla_spec.read_text()
    assert 'Release: 2.2%{?dist}' in spec_content

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: second rebuild'


def test_rebuild_dry_run(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test --dry-run prevents commit."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    initial_subject, _ = get_last_commit_info(workdir)

    # Rebuild with --dry-run
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'rebuild', 'vanilla',
         '--reason', 'test'],
        cwd=workdir, check=True,
    )

    # Verify spec was modified
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    assert 'Release: 1.1%{?dist}' in spec_content

    # Verify no new commit
    subject, _ = get_last_commit_info(workdir)
    assert subject == initial_subject

    # Verify git status shows modified files
    status_result = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    assert 'vanilla.spec' in status_result.stdout


def test_rebuild_preserves_macros(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild preserves macros in Release field."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify to have Release with macro: 8.%{revision}%{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    # After import, the file has %{?dist} added, so replace the whole line
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 8.%{revision}%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro in Release'], cwd=workdir, check=True)

    # Rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test macro preservation'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped but macro preserved
    spec_content = vanilla_spec.read_text()
    assert 'Release: 8.%{revision}.1%{?dist}' in spec_content


def test_rebuild_nonexistent_package(workdir: Path) -> None:
    """Test rebuild fails gracefully for nonexistent package."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'nonexistent',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "Package 'nonexistent' not found" in result.stderr


def test_bump_release_helper(dist_git_module) -> None:
    """Test bump_release helper function logic."""
    bump = dist_git_module.bump_release

    # Basic bump
    assert bump('3') == '3.1'
    assert bump('1') == '1.1'

    # Already bumped
    assert bump('3.1') == '3.2'
    assert bump('3.2') == '3.3'
    assert bump('1.99') == '1.100'

    # With macros
    assert bump('8.%{revision}') == '8.%{revision}.1'
    assert bump('8.%{revision}.1') == '8.%{revision}.2'

    # Complex release strings
    assert bump('10') == '10.1'
    assert bump('0') == '0.1'

    # With upstream_release parameter (smart bumping for Fedora releases with dots)
    # Scenario: Fedora ships Release: 3.1, we rebuild it -> should become 3.1.1
    assert bump('3.1', upstream_release='3.1') == '3.1.1'

    # Scenario: We already rebuilt Fedora's 3.1 to 3.1.1, rebuild again -> 3.1.2
    assert bump('3.1.1', upstream_release='3.1') == '3.1.2'

    # Scenario: Fedora ships Release: 5, we rebuild it -> 5.1 (same as before)
    assert bump('5', upstream_release='5') == '5.1'

    # Scenario: We already rebuilt Fedora's 3 to 3.1, rebuild again -> 3.2
    # (upstream is still 3, current is 3.1, so current != upstream -> increment logic)
    assert bump('3.1', upstream_release='3') == '3.2'

    # Scenario: No upstream info (None) - falls back to old behavior
    assert bump('3.1', upstream_release=None) == '3.2'
    assert bump('3', upstream_release=None) == '3.1'

    # Real-world release strings from the repo:

    # libyuv: ends with non-digit letters, so no .N match -> append .1
    assert bump('0.62.20260213git6067afd') == '0.62.20260213git6067afd.1'
    assert bump('0.62.20260213git6067afd.1') == '0.62.20260213git6067afd.2'

    # libedit: macro with non-digit suffix -> no .N match -> append .1
    assert bump('58.%{snap}cvs') == '58.%{snap}cvs.1'
    assert bump('58.%{snap}cvs.1') == '58.%{snap}cvs.2'

    # ansible-packaging: Fedora ships 20.1 (dotted), first rebuild -> 20.1.1
    assert bump('20.1', upstream_release='20.1') == '20.1.1'
    assert bump('20.1.1', upstream_release='20.1') == '20.1.2'

    # python-gitlab: Fedora ships 1.1, already rebuilt to 1.2 -> 1.3
    assert bump('1.2', upstream_release='1.1') == '1.3'

    # Fedora pre-release (0.N pattern): first rebuild -> 0.1.1, not 0.2
    assert bump('0.1', upstream_release='0.1') == '0.1.1'
    assert bump('0.1.1', upstream_release='0.1') == '0.1.2'


def test_rebuild_all(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild --all rebuilds all packages with one commit per package."""
    # Import two packages
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Get commit count before rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_before = int(result.stdout.strip())

    # Rebuild all packages
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--all',
         '--reason', 'test rebuild all'],
        cwd=workdir, check=True,
    )

    # Get commit count after rebuild
    result = subprocess.run(
        ['git', 'rev-list', '--count', 'HEAD'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commits_after = int(result.stdout.strip())

    # Should have created 2 commits (one per package)
    assert commits_after - commits_before == 2

    # Verify both spec files were updated
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1.1%{?dist}' in chocolate_spec.read_text()

    # Verify commit messages
    result = subprocess.run(
        ['git', 'log', '--format=%s', '-2'],
        cwd=workdir, capture_output=True, check=True, text=True
    )
    commit_subjects = result.stdout.strip().split('\n')
    # Commits are in reverse order (newest first)
    assert 'Rebuild chocolate: test rebuild all' in commit_subjects or 'Rebuild vanilla: test rebuild all' in commit_subjects
    assert len([s for s in commit_subjects if 'Rebuild' in s and 'test rebuild all' in s]) == 2


def test_rebuild_all_vs_package_mutually_exclusive(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that --all and package name are mutually exclusive."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Should fail when both --all and package name are specified
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--all',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'Cannot specify both package names and --all' in result.stderr


def test_rebuild_requires_package_or_all(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild requires either package name or --all."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Should fail when neither --all nor package name is specified
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'Must specify either package name(s) or --all' in result.stderr


def test_rebuild_rejects_macro_without_dist(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild rejects macros that don't end with %{?dist}."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify spec to use a macro in Release field WITHOUT %{?dist}
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{some_macro}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro in Release'], cwd=workdir, check=True)

    # Rebuild should fail with clear error
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'test'],
        cwd=workdir, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert 'uses macros in Release field without' in result.stderr
    assert 'requires manual rebuild' in result.stderr


def test_rebuild_macro_with_dist(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild handles macro-based Release that ends with %{?dist}."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify spec to use a macro in Release field WITH %{?dist}
    # Simulates packages like rpm (%{baserelease}%{?dist}) or gcc (%{gcc_release}%{?dist})
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{my_macro}%{?dist}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Use macro with dist'], cwd=workdir, check=True)

    # First rebuild: should add .1
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'first rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was updated correctly
    spec_content = vanilla_spec.read_text()
    assert 'Release: %{my_macro}.1%{?dist}' in spec_content

    # Second rebuild: should increment to .2
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was incremented
    spec_content = vanilla_spec.read_text()
    assert 'Release: %{my_macro}.2%{?dist}' in spec_content


def test_rebuild_with_content_after_dist(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild preserves content after %{?dist}."""
    # Import vanilla
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify spec to have content after %{?dist} (like unbound)
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: 5%{?dist} %{?extra_version:-e %{extra_version}}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Add extra_version'], cwd=workdir, check=True)

    # Rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'first rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was updated and content after dist was preserved
    spec_content = vanilla_spec.read_text()
    assert 'Release: 5.1%{?dist} %{?extra_version:-e %{extra_version}}' in spec_content

    # Second rebuild
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla',
         '--reason', 'second rebuild'],
        cwd=workdir, check=True,
    )

    # Verify increment and preservation
    spec_content = vanilla_spec.read_text()
    assert 'Release: 5.2%{?dist} %{?extra_version:-e %{extra_version}}' in spec_content


def test_rebuild_real_world_release_strings(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild handles real-world Release field patterns seen in the repo."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'

    cases = [
        # (initial Release line, expected after first rebuild, expected after second rebuild)
        # libyuv-style: long timestamp string with no trailing digit
        ('0.62.20260213git6067afd%{?dist}',
         '0.62.20260213git6067afd.1%{?dist}',
         '0.62.20260213git6067afd.2%{?dist}'),
        # ansible-packaging-style: Fedora ships dotted release (20.1)
        # Upstream release in metadata is also "20.1", so first rebuild -> 20.1.1
        ('20.1%{?dist}', '20.1.1%{?dist}', '20.1.2%{?dist}'),
        # Fedora pre-release pattern (0.N): must not be treated as already-bumped
        ('0.1%{?dist}', '0.1.1%{?dist}', '0.1.2%{?dist}'),
        # Content after %{?dist} preserved (unbound-style)
        ('11%{?dist} %{?extra_version:-e %{extra_version}}',
         '11.1%{?dist} %{?extra_version:-e %{extra_version}}',
         '11.2%{?dist} %{?extra_version:-e %{extra_version}}'),
    ]

    for initial, after_first, after_second in cases:
        # Set Release to initial value and update metadata to match (simulates fresh import)
        spec_content = vanilla_spec.read_text()
        modified = re.sub(r'^Release:.*$', f'Release: {initial}', spec_content, flags=re.MULTILINE)
        vanilla_spec.write_text(modified)

        # Update metadata release to the plain value (without %{?dist} and trailing content)
        metadata_file = workdir / 'metadata' / 'vanilla.json'
        import json
        metadata = json.loads(metadata_file.read_text())
        # Strip %{?dist} and anything after it so upstream_release matches what bump_release expects
        upstream_rel = re.sub(r'%\{\??dist\}.*', '', initial).strip()
        metadata['release'] = upstream_rel
        metadata_file.write_text(json.dumps(metadata, indent=2) + '\n')

        subprocess.run(['git', 'commit', '-a', '-m', f'Set Release: {initial}'],
                       cwd=workdir, check=True)

        # First rebuild
        subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--reason', 'first'],
            cwd=workdir, check=True,
        )
        assert after_first in vanilla_spec.read_text(), \
            f"After first rebuild of {initial!r}: expected {after_first!r}"

        # Second rebuild (upstream_release in metadata is still the original)
        subprocess.run(
            [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'vanilla', '--reason', 'second'],
            cwd=workdir, check=True,
        )
        assert after_second in vanilla_spec.read_text(), \
            f"After second rebuild of {initial!r}: expected {after_second!r}"


def test_rebuild_all_continues_on_failure(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test that rebuild --all continues processing when some packages fail."""
    # Import two packages
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Break vanilla by using a macro in Release
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = re.sub(
        r'^Release:.*$',
        'Release: %{broken_macro}',
        spec_content,
        flags=re.MULTILINE
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Break vanilla'], cwd=workdir, check=True)

    # Rebuild all with --dry-run
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'rebuild', '--all',
         '--reason', 'test all'],
        cwd=workdir, capture_output=True, text=True
    )

    # Should exit with error code since one package failed
    assert result.returncode == 1

    # Should show failure for vanilla
    assert 'Failed to rebuild vanilla' in result.stderr
    assert 'uses macros in Release field' in result.stderr

    # Should show success for chocolate
    assert 'Rebuilding chocolate' in result.stderr
    assert 'Updated Release: to 1.1%{?dist} in chocolate.spec' in result.stderr

    # Should show summary
    assert 'Rebuild Summary:' in result.stdout
    assert 'Total packages: 2' in result.stdout
    assert 'Failed rebuilds: 1' in result.stdout
    assert 'Failed packages:' in result.stdout
    assert '- vanilla' in result.stdout

    # chocolate should be modified (in dry-run), vanilla should not
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    assert 'Release: 1.1%{?dist}' in chocolate_spec.read_text()

    # vanilla should still have the broken macro
    assert 'Release: %{broken_macro}' in vanilla_spec.read_text()


def test_rebuild_native_package(workdir: Path) -> None:
    """Test that rebuilding a native package bumps release correctly."""
    # Create a native package manually
    native_dir = workdir / 'rpms' / 'native-pkg'
    native_dir.mkdir()

    # Create spec file with Release: 0.1%{?dist}
    native_spec = native_dir / 'native-pkg.spec'
    native_spec.write_text("""Name: native-pkg
Version: 1.0
Release: 0.1%{?dist}
Summary: Test native package
License: MIT

%description
Test native package

%files
""")

    # Create metadata for native package (no 'source' field)
    metadata_file = workdir / 'metadata' / 'native-pkg.json'
    metadata = {
        'version': '1.0',
        'release': '0.1',
        'modification_status': 'native'
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
        f.write('\n')

    # Commit the native package
    subprocess.run(['git', 'add', 'rpms/native-pkg', 'metadata/native-pkg.json'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add native package'], cwd=workdir, check=True)

    # Rebuild the native package
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild', 'native-pkg',
         '--reason', 'test native rebuild'],
        cwd=workdir, check=True,
    )

    # Verify Release was bumped to 0.2, not 0.1.1
    spec_content = native_spec.read_text()
    assert 'Release: 0.2%{?dist}' in spec_content, f"Expected '0.2', got spec:\n{spec_content}"

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild native-pkg: test native rebuild'


def test_rebuild_rev_deps_direct(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with direct package name (go-rpm-macros)."""
    # Import vanilla which BuildRequires: go-rpm-macros (simulated)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Modify vanilla spec to add BuildRequires: go-rpm-macros
    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace(
        'Summary: Test package vanilla',
        'Summary: Test package vanilla\nBuildRequires: go-rpm-macros'
    )
    vanilla_spec.write_text(modified_content)
    subprocess.run(['git', 'commit', '-a', '-m', 'Add BuildRequires'], cwd=workdir, check=True)

    # Rebuild reverse dependencies of go-rpm-macros
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'go-rpm-macros',
         '--reason', 'go-rpm-macros update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should rebuild vanilla
    assert 'Rebuilding vanilla: go-rpm-macros update' in result.stderr
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    # Check summary
    assert 'Rebuild Summary for go-rpm-macros reverse dependencies:' in result.stdout
    assert 'Total packages: 1' in result.stdout
    assert 'Successful rebuilds: 1' in result.stdout


def setup_golang_packages(workdir: Path, versions: list[str]) -> None:
    """Create golang packages for testing virtual BuildRequires."""
    for version in versions:
        golang_dir = workdir / 'rpms' / f'golang{version}'
        golang_dir.mkdir()
        golang_spec = golang_dir / 'golang.spec'
        golang_spec.write_text(f"""Name: golang{version}
Version: {version}.0
Release: 1%{{?dist}}
Summary: Go compiler {version}
License: BSD
Provides: golang = %{{version}}-%{{release}}

%description
Go compiler version {version}

%files
""")
        metadata_file = workdir / 'metadata' / f'golang{version}.json'
        metadata = {
            'source': 'https://example.com/golang.git',
            'branch': 'rawhide',
            'sha': '1234567890abcdef',
            'version': f'{version}.0',
            'release': '1',
            'modification_status': 'clean'
        }
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
            f.write('\n')


def test_rebuild_rev_deps_virtual_buildrequires_latest(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with latest virtual BuildRequires (golang1.26)."""
    setup_golang_packages(workdir, ['1.25', '1.26'])

    # Import vanilla which BuildRequires: go-rpm-macros
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    vanilla_spec = workdir / 'rpms' / 'vanilla' / 'vanilla.spec'
    spec_content = vanilla_spec.read_text()
    modified_content = spec_content.replace(
        'Summary: Test package vanilla',
        'Summary: Test package vanilla\nBuildRequires: go-rpm-macros'
    )
    vanilla_spec.write_text(modified_content)

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add golang packages and BR'], cwd=workdir, check=True)

    # Rebuild reverse dependencies of golang1.26 (latest)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'golang1.26',
         '--reason', 'golang 1.26 update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should translate golang1.26 to go-rpm-macros and rebuild vanilla
    assert 'Translating golang1.26 to go-rpm-macros (virtual BuildRequires pattern)' in result.stderr
    assert 'Rebuilding vanilla: golang 1.26 update' in result.stderr
    assert 'Release: 1.1%{?dist}' in vanilla_spec.read_text()

    # Check commit message
    subject, _ = get_last_commit_info(workdir)
    assert subject == 'Rebuild vanilla: golang 1.26 update'


def test_rebuild_rev_deps_virtual_buildrequires_not_latest(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps rejects non-latest virtual BuildRequires."""
    setup_golang_packages(workdir, ['1.25', '1.26'])

    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add golang packages'], cwd=workdir, check=True)

    # Try to rebuild reverse dependencies of golang1.25 (not latest)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'golang1.25',
         '--reason', 'golang 1.25 update'],
        cwd=workdir, capture_output=True, text=True
    )

    # Should exit with error
    assert result.returncode != 0
    assert 'golang1.25 is not the latest version' in result.stderr
    assert 'Latest version is golang1.26' in result.stderr


def test_rebuild_rev_deps_no_dependencies(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Test rebuild-rev-deps with package that has no reverse dependencies."""
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["vanilla"]}'],
        cwd=workdir, check=True,
    )

    # Rebuild reverse dependencies of vanilla (which nothing depends on)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'rebuild-rev-deps', 'vanilla',
         '--reason', 'vanilla update'],
        cwd=workdir, capture_output=True, text=True, check=True
    )

    # Should report no dependencies
    assert 'No packages found with BuildRequires: vanilla' in result.stderr


def test_ls_sources() -> None:
    """Integration test: ls-sources on tar"""

    # This calls the actual dist-git-client and thus downloads the archives
    # (in the first run). This is a bit awkward for a unit test, but otherwise
    # we can't meaningfully test this.
    tar_dir = project_root / 'rpms' / 'tar'

    result = subprocess.run(
        [str(project_root / 'ci' / 'dist_git.py'), 'ls-sources', 'tar'],
        cwd=project_root, capture_output=True, text=True, check=True)

    # Verify output contains archive headers
    assert re.search(r'==== tar-[\d.]+\.tar\.xz ====', result.stdout)
    assert re.search(r'==== tar-[\d.]+\.tar\.xz\.sig ====', result.stdout)

    # Verify output contains some tar contents
    assert re.search(r'tar-[\d.]+/', result.stdout)
    assert 'configure' in result.stdout

    # Verify files were actually downloaded
    assert len(list(tar_dir.glob('tar-*.tar.xz'))) == 1
    assert len(list(tar_dir.glob('tar-*.tar.xz.sig'))) == 1


#
# --branch flag tests
#

def test_update_with_branch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch switches to a different upstream branch."""
    # Import chocolate from rawhide (version 10)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update with --branch f40 (version 4)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f40'
    assert metadata['version'] == '4'

    # Verify spec file has f40 content
    spec = (workdir / 'rpms' / 'chocolate' / 'chocolate.spec').read_text()
    assert 'Version: 4' in spec

    # Verify commit message includes branch change
    subject, body = get_last_commit_info(workdir)
    assert '(rawhide -> f40)' in subject
    assert 'Upstream:' in body


def test_update_with_branch_release_upgrade(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch from one numbered release to a higher one (f40 -> f99)."""
    # Import chocolate from f40 (version 4)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--branch', 'f40',
         f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Update with --branch f99 (version 99)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f99',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f99'
    assert metadata['version'] == '99'

    # Verify commit message includes branch change
    subject, _ = get_last_commit_info(workdir)
    assert '(f40 -> f99)' in subject


def test_update_with_branch_modified_package(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch merges local modifications across branch change."""
    # Import chocolate from rawhide (version 10)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Add a local modification (extra comment in spec) and mark as modified
    spec_path = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = spec_path.read_text()
    spec_path.write_text(spec_content + '# Hummingbird local modification\n')
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add local modification'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', '--modified',
         '--reason', 'test modification', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Update with --branch f40 (version 4) — should merge our local mod
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f40'
    assert metadata['version'] == '4'
    assert metadata['modification_status'] == 'modified'

    # Verify local modification survived the merge
    spec_content = spec_path.read_text()
    assert '# Hummingbird local modification' in spec_content
    assert 'Version: 4' in spec_content


def test_update_with_branch_same_branch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch same as current behaves like normal update."""
    # Import chocolate from rawhide (version 10)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Add upstream commit on rawhide
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '10', '11')

    # Update with --branch rawhide (same branch)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'rawhide',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify normal update happened
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'rawhide'
    assert metadata['version'] == '11'

    # Verify commit message does NOT include branch change annotation
    subject, _ = get_last_commit_info(workdir)
    assert '->' not in subject


def test_update_with_branch_requires_package(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch but no package name fails with error."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert '--branch requires a package name' in result.stderr


def test_update_with_branch_lower_version(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Forward branch move allows version downgrade; backward move blocks it."""
    # Import chocolate from f99 (version 99)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', '--branch', 'f99',
         f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # f99 -> f40 is a backward move — version downgrade should be blocked
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    # The update should silently skip (exit 0) with a warning about older version
    assert result.returncode == 0
    assert 'upstream version 4 is older than current 99' in result.stderr

    # Verify metadata NOT changed (branch still f99)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f99'
    assert metadata['version'] == '99'

    # rawhide -> f40 IS a forward move (rawhide resolves to f99, the highest)
    # Re-import from rawhide first
    subprocess.run(['git', 'rm', '-rf', 'rpms/chocolate', 'metadata/chocolate.json'],
                   cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Remove chocolate'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # rawhide (v10) -> f40 (v4): version goes down but rawhide->f40 is forward
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f40'
    assert metadata['version'] == '4'


def test_sync_with_branch(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Sync with --branch switches branch and discards local modifications."""
    # Import chocolate from rawhide (version 10)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Add local modification and mark as modified
    spec_path = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = spec_path.read_text()
    spec_path.write_text(spec_content + '# Local change to discard\n')
    subprocess.run(['git', 'add', '-A'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Local change'], cwd=workdir, check=True)

    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'mark-modified', '--modified',
         '--reason', 'test', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Sync with --branch f40
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'sync', '--branch', 'f40', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify metadata updated and status reset
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f40'
    assert metadata['version'] == '4'
    assert metadata['modification_status'] == 'clean'

    # Verify local modification discarded
    spec_content = spec_path.read_text()
    assert '# Local change to discard' not in spec_content
    assert 'Version: 4' in spec_content

    # Verify commit message
    subject, _ = get_last_commit_info(workdir)
    assert 'Sync' in subject
    assert '(rawhide -> f40)' in subject


def test_update_with_branch_and_ref(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Update with --branch and --ref uses specific commit from new branch."""
    # Import chocolate from rawhide (version 10)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Add two commits to f40 branch
    subprocess.run(['git', 'checkout', 'f40'], cwd=upstream_repos["chocolate"], check=True)
    intermediate_sha = add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '4', '5')
    add_upstream_commit(upstream_repos["chocolate"], 'chocolate', '5', '6')
    subprocess.run(['git', 'checkout', 'rawhide'], cwd=upstream_repos["chocolate"], check=True)

    # Update with --branch f40 --ref to intermediate commit (version 5, not 6)
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--branch', 'f40',
         '--ref', intermediate_sha, '--skip-build-check', 'chocolate'],
        cwd=workdir, check=True,
    )

    # Verify we got version 5, not 6
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        metadata = json.load(f)
    assert metadata['branch'] == 'f40'
    assert metadata['version'] == '5'
    assert metadata['sha'] == intermediate_sha


#
# --continue flag tests
#

def _create_conflict(workdir: Path, upstream_repos: dict[str, Path]) -> str:
    """Set up a conflicted update and return the new upstream SHA.

    Imports chocolate, makes a local License change, makes a conflicting upstream
    change, and runs update which exits with code 2.
    """
    # Import chocolate from rawhide
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'import', f'file://{upstream_repos["chocolate"]}'],
        cwd=workdir, check=True,
    )

    # Make local modification: change License line
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    chocolate_spec.write_text(spec_content.replace('License: GPL', 'License: MIT'))
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), '--dry-run', 'mark-modified', '--modified',
         '--reason', 'Local license change', 'chocolate'], cwd=workdir, check=True,
    )
    subprocess.run(['git', 'commit', '-a', '-m', 'Local modification'], cwd=workdir, check=True)

    # Make conflicting upstream change
    upstream_spec = upstream_repos['chocolate'] / 'chocolate.spec'
    upstream_content = upstream_spec.read_text()
    updated = upstream_content.replace('License: GPL', 'License: Apache-2.0')
    updated = updated.replace('Version: 10', 'Version: 11')
    upstream_spec.write_text(updated)
    subprocess.run(['git', 'commit', '-a', '-m', 'Change license to Apache'],
                   cwd=upstream_repos['chocolate'], check=True)

    new_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=upstream_repos['chocolate'], capture_output=True, text=True, check=True
    ).stdout.strip()

    # Run update — exits with code 2 (conflicts)
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--skip-build-check', 'chocolate'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 2
    return new_sha


def test_update_continue(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """Full conflict -> resolve -> --continue flow."""
    new_sha = _create_conflict(workdir, upstream_repos)

    # Resolve conflicts by picking our License
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    # Run --continue
    subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, check=True,
    )

    # Verify commit was created with correct message
    subject, body = get_last_commit_info(workdir)
    assert 'Update chocolate from 10-1 to 11-1' in subject
    assert f'Upstream: {new_sha}' in body

    # Verify state file was cleaned up
    assert not (workdir / '.dist_git_update_state.json').exists()

    # Verify resolved spec is committed (no conflict markers)
    committed_spec = chocolate_spec.read_text()
    assert '<<<<<<' not in committed_spec
    assert 'License: MIT' in committed_spec

    # Verify metadata was restored to the updated version (HUM-2672)
    with open(workdir / 'metadata' / 'chocolate.json') as f:
        committed_metadata = json.load(f)
    assert committed_metadata['version'] == '11', \
        "Metadata committed by --continue should contain the updated version"


def test_update_continue_no_state(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with no state file exits with error."""
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'No update in progress' in result.stderr


def test_update_continue_unresolved(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with remaining conflict markers exits with error."""
    _create_conflict(workdir, upstream_repos)

    # Try --continue WITHOUT resolving conflicts
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert 'Unresolved conflicts' in result.stderr

    # Verify state file still exists (for retry)
    assert (workdir / '.dist_git_update_state.json').exists()


def test_update_continue_legacy_state(workdir: Path, upstream_repos: dict[str, Path]) -> None:
    """--continue with a pre-HUM-2672 state file (no 'metadata' key) should not crash.

    Old state files written before the HUM-2672 fix lack the 'metadata' key.
    continue_update() should still work, using whatever metadata is on disk.
    """
    _create_conflict(workdir, upstream_repos)

    # Overwrite state file with the old format (no 'metadata' key)
    state_file = workdir / '.dist_git_update_state.json'
    with open(state_file) as f:
        state = json.load(f)
    state.pop('metadata', None)
    with open(state_file, 'w') as f:
        json.dump(state, f)

    # Resolve conflicts
    chocolate_spec = workdir / 'rpms' / 'chocolate' / 'chocolate.spec'
    spec_content = chocolate_spec.read_text()
    resolved = re.sub(r'<<<<<<< HEAD\n.*?=======\n(.*?)>>>>>>> hummingbird-local\n',
                      r'\1', spec_content, flags=re.DOTALL)
    chocolate_spec.write_text(resolved)

    # --continue should succeed without error
    result = subprocess.run(
        [str(workdir / 'ci' / 'dist_git.py'), 'update', '--continue'],
        cwd=workdir, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"--continue failed: {result.stderr}"
    assert not state_file.exists()
