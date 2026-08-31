"""Tests for check_upstream_versions."""

import json
import subprocess
import types
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


#
# Fixtures
#


@pytest.fixture
def cuv_module():
    """Load check_upstream_versions.py as a Python module."""
    script_path = Path(__file__).parent.parent / 'ci' / 'check_upstream_versions.py'
    module = types.ModuleType("check_upstream_versions")
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)
    return module


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Create a minimal repo layout for testing."""
    subprocess.run(['git', 'init'], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=tmp_path, check=True)

    (tmp_path / 'rpms').mkdir()
    (tmp_path / 'metadata').mkdir()

    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit', '--allow-empty'],
                   cwd=tmp_path, check=True)

    return tmp_path


def _create_package(workdir: Path, name: str, version: str,
                    sources: dict | None = None,
                    metadata: dict | None = None) -> Path:
    """Create a package directory with a spec file.

    Args:
        workdir: Repository root
        name: Package name
        version: Package version
        sources: Optional dict mapping filename -> hash for the sources file
        metadata: Optional metadata dict (written to metadata/<name>.json)

    Returns:
        Path to the package directory
    """
    pkg_dir = workdir / 'rpms' / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    spec = pkg_dir / f'{name}.spec'
    spec.write_text(f"""Name: {name}
Version: {version}
Release: 1%{{?dist}}
Summary: Test package {name}
License: MIT
Source0: https://example.com/{name}/{name}-%{{version}}.tar.gz

%description
Test package

%files
""")

    if sources:
        lines = [f"SHA512 ({fn}) = {h}" for fn, h in sources.items()]
        (pkg_dir / 'sources').write_text('\n'.join(lines) + '\n')

    if metadata is not None:
        meta_file = workdir / 'metadata' / f'{name}.json'
        with open(meta_file, 'w') as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
            f.write('\n')

    return pkg_dir


def test_run_git_logs_start_and_completion(cuv_module, caplog) -> None:
    """Git command boundaries and elapsed time are visible in CI logs."""
    completed = subprocess.CompletedProcess(['git', 'status'], 0, stdout='')

    with patch.object(cuv_module.subprocess, 'run', return_value=completed), \
         patch.object(cuv_module.time, 'monotonic', side_effect=[10.0, 12.5]), \
         caplog.at_level('INFO'):
        cuv_module.run_git('status')

    assert 'Running command: git status' in caplog.text
    assert 'Command completed after 2.5s: git status' in caplog.text


def test_run_git_logs_failure(cuv_module, caplog) -> None:
    """Failed Git commands report their elapsed time before propagating."""
    error = subprocess.CalledProcessError(1, ['git', 'commit'])

    with patch.object(cuv_module.subprocess, 'run', side_effect=error), \
         patch.object(cuv_module.time, 'monotonic', side_effect=[10.0, 11.0]), \
         caplog.at_level('INFO'), \
         pytest.raises(subprocess.CalledProcessError):
        cuv_module.run_git('commit')

    assert 'Command failed after 1.0s: git commit' in caplog.text


#
# Tests — compare_versions
#


def test_compare_versions_newer(cuv_module) -> None:
    """Upstream newer than current returns 1."""
    assert cuv_module.compare_versions('1.0', '2.0') == 1


def test_compare_versions_equal(cuv_module) -> None:
    """Equal versions return 0."""
    assert cuv_module.compare_versions('1.0', '1.0') == 0


def test_compare_versions_older(cuv_module) -> None:
    """Upstream older than current returns -1."""
    assert cuv_module.compare_versions('2.0', '1.0') == -1


def test_compare_versions_complex(cuv_module) -> None:
    """Multi-component version comparison."""
    assert cuv_module.compare_versions('1.2.3', '1.2.4') == 1
    assert cuv_module.compare_versions('1.2.3', '1.3.0') == 1
    assert cuv_module.compare_versions('1.10.0', '1.9.0') == -1


#
# Tests — parse_spec_version
#


def test_parse_spec_version(cuv_module, workdir: Path) -> None:
    """Extract version from a spec file."""
    pkg_dir = _create_package(workdir, 'testpkg', '3.5.1')
    assert cuv_module.parse_spec_version(pkg_dir) == '3.5.1'


def test_parse_spec_version_no_spec(cuv_module, tmp_path: Path) -> None:
    """Returns None when no spec file exists."""
    assert cuv_module.parse_spec_version(tmp_path) is None


def test_parse_spec_version_multiple_specs(cuv_module, tmp_path: Path) -> None:
    """Returns None when multiple spec files exist."""
    (tmp_path / 'a.spec').write_text('Name: a\nVersion: 1\n')
    (tmp_path / 'b.spec').write_text('Name: b\nVersion: 2\n')
    assert cuv_module.parse_spec_version(tmp_path) is None


#
# Tests — parse_spec_version_release
#


def test_parse_spec_version_release(cuv_module, workdir: Path) -> None:
    """Extract version and release from a spec file."""
    pkg_dir = _create_package(workdir, 'testpkg', '3.5.1')
    result = cuv_module.parse_spec_version_release(pkg_dir)
    assert result is not None
    assert result[0] == '3.5.1'
    assert result[1] == '1'


def test_parse_spec_version_release_no_spec(cuv_module, tmp_path: Path) -> None:
    """Returns None when no spec file exists."""
    assert cuv_module.parse_spec_version_release(tmp_path) is None


#
# Tests — get_version_from_metadata
#


def test_get_version_from_metadata(cuv_module, workdir: Path) -> None:
    """Read version from metadata JSON."""
    _create_package(workdir, 'mypkg', '1.0',
                    metadata={'version': '2.5', 'release': '1'})
    cuv_module.METADATA_DIR = workdir / 'metadata'
    assert cuv_module.get_version_from_metadata('mypkg') == '2.5'


def test_get_version_from_metadata_missing(cuv_module, workdir: Path) -> None:
    """Returns None when metadata file does not exist."""
    cuv_module.METADATA_DIR = workdir / 'metadata'
    assert cuv_module.get_version_from_metadata('nonexistent') is None


#
# Tests — _parse_sources_file / _write_sources_file
#


def test_parse_sources_file(cuv_module, tmp_path: Path) -> None:
    """Parse BSD-style sources file."""
    sources = tmp_path / 'sources'
    sources.write_text(
        'SHA512 (foo-1.0.tar.gz) = abc123\n'
        'SHA512 (foo-1.0.tar.gz.sig) = def456\n'
    )

    entries = cuv_module._parse_sources_file(sources)
    assert len(entries) == 2
    assert entries[0]['algo'] == 'SHA512'
    assert entries[0]['filename'] == 'foo-1.0.tar.gz'
    assert entries[0]['hash'] == 'abc123'
    assert entries[1]['filename'] == 'foo-1.0.tar.gz.sig'


def test_parse_sources_file_missing(cuv_module, tmp_path: Path) -> None:
    """Returns empty list when sources file does not exist."""
    entries = cuv_module._parse_sources_file(tmp_path / 'sources')
    assert entries == []


def test_parse_sources_file_empty(cuv_module, tmp_path: Path) -> None:
    """Returns empty list for an empty sources file."""
    sources = tmp_path / 'sources'
    sources.write_text('')
    entries = cuv_module._parse_sources_file(sources)
    assert entries == []


def test_write_sources_file(cuv_module, tmp_path: Path) -> None:
    """Round-trip: write and re-parse sources file."""
    sources = tmp_path / 'sources'
    entries = [
        {'algo': 'SHA512', 'filename': 'pkg-2.0.tar.xz', 'hash': 'aaa'},
        {'algo': 'SHA512', 'filename': 'pkg-2.0.tar.xz.sig', 'hash': 'bbb'},
    ]
    cuv_module._write_sources_file(sources, entries)

    parsed = cuv_module._parse_sources_file(sources)
    assert len(parsed) == 2
    assert parsed[0]['filename'] == 'pkg-2.0.tar.xz'
    assert parsed[1]['hash'] == 'bbb'


#
# Tests — _compute_file_hash
#


def test_compute_file_hash(cuv_module, tmp_path: Path) -> None:
    """Compute SHA512 hash of a file."""
    f = tmp_path / 'data.bin'
    f.write_bytes(b'hello world')
    h = cuv_module._compute_file_hash(f, 'SHA512')
    assert len(h) == 128  # SHA-512 hex digest length
    # Same content produces same hash
    assert h == cuv_module._compute_file_hash(f, 'SHA512')


def test_compute_file_hash_sha256(cuv_module, tmp_path: Path) -> None:
    """Compute SHA256 hash of a file."""
    f = tmp_path / 'data.bin'
    f.write_bytes(b'test data')
    h = cuv_module._compute_file_hash(f, 'SHA256')
    assert len(h) == 64  # SHA-256 hex digest length


#
# Tests — _update_gitignore
#


def test_update_gitignore_creates_file(cuv_module, tmp_path: Path) -> None:
    """Creates .gitignore when it does not exist."""
    cuv_module._update_gitignore(tmp_path, ['foo-1.0.tar.gz', 'foo-1.0.tar.gz.sig'])
    gitignore = tmp_path / '.gitignore'
    assert gitignore.exists()
    lines = gitignore.read_text().splitlines()
    assert 'foo-1.0.tar.gz' in lines
    assert 'foo-1.0.tar.gz.sig' in lines


def test_update_gitignore_appends(cuv_module, tmp_path: Path) -> None:
    """Appends to existing .gitignore without duplicating entries."""
    gitignore = tmp_path / '.gitignore'
    gitignore.write_text('existing-entry\nfoo-1.0.tar.gz\n')

    cuv_module._update_gitignore(tmp_path, ['foo-1.0.tar.gz', 'bar-2.0.tar.xz'])

    lines = gitignore.read_text().splitlines()
    assert lines.count('foo-1.0.tar.gz') == 1  # not duplicated
    assert 'bar-2.0.tar.xz' in lines
    assert 'existing-entry' in lines


def test_update_gitignore_no_duplicates(cuv_module, tmp_path: Path) -> None:
    """Does not write anything when all entries already exist."""
    gitignore = tmp_path / '.gitignore'
    gitignore.write_text('a.tar.gz\nb.tar.gz\n')

    cuv_module._update_gitignore(tmp_path, ['a.tar.gz', 'b.tar.gz'])

    content = gitignore.read_text()
    assert content == 'a.tar.gz\nb.tar.gz\n'


def test_update_gitignore_empty_list(cuv_module, tmp_path: Path) -> None:
    """No-op when filenames list is empty."""
    cuv_module._update_gitignore(tmp_path, [])
    assert not (tmp_path / '.gitignore').exists()


#
# Tests — mark_package_modified
#


def test_mark_package_modified(cuv_module, workdir: Path) -> None:
    """Sets modification_status to modified and modification_reason."""
    _create_package(workdir, 'pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1'})
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.mark_package_modified('pkg', 'Update to upstream version 2.0')

    with open(workdir / 'metadata' / 'pkg.json') as f:
        data = json.load(f)
    assert data['modification_status'] == 'modified'
    assert data['modification_reason'] == 'Update to upstream version 2.0'


def test_mark_package_modified_preserves_independent(cuv_module, workdir: Path) -> None:
    """Does not overwrite independent status with modified."""
    _create_package(workdir, 'pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'modification_status': 'independent'})
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.mark_package_modified('pkg', 'Update to upstream version 2.0',
                                     version='2.0')

    with open(workdir / 'metadata' / 'pkg.json') as f:
        data = json.load(f)
    assert data['modification_status'] == 'independent'
    assert 'modification_reason' not in data
    assert data['version'] == '2.0'
    assert 'release' not in data


def test_mark_package_modified_removes_fedora_release(cuv_module, workdir: Path) -> None:
    """Removes the Fedora release after an upstream version bump."""
    _create_package(workdir, 'pkg', '1.0', metadata={
        'version': '1.0', 'release': '1', 'source': 'https://example.com',
    })
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.mark_package_modified('pkg', 'Update to upstream version 2.0', version='2.0')

    data = json.loads((workdir / 'metadata' / 'pkg.json').read_text())
    assert data['version'] == '2.0'
    assert 'release' not in data


def test_mark_package_modified_no_metadata(cuv_module, workdir: Path) -> None:
    """Logs warning and does nothing when metadata file is missing."""
    cuv_module.METADATA_DIR = workdir / 'metadata'
    # Should not raise
    cuv_module.mark_package_modified('nonexistent', 'reason')


#
# Tests — check_package_version
#


def test_check_package_version_has_update(cuv_module, workdir: Path) -> None:
    """Detects when upstream version is newer."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0', '1.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    assert result.has_update is True
    assert result.current_version == '1.0'
    assert result.upstream_version == '2.0'
    assert result.anitya_project_id == 42


def test_check_package_version_up_to_date(cuv_module, workdir: Path) -> None:
    """No update when current matches upstream."""
    _create_package(workdir, 'pkg', '2.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    assert result.has_update is False
    assert result.current_version == '2.0'
    assert result.upstream_version == '2.0'


def test_check_package_version_not_found(cuv_module, workdir: Path) -> None:
    """Handles package not found in Anitya."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    with patch.object(cuv_module, 'query_anitya',
                      side_effect=ValueError('Package not found in Fedora')):
        result = cuv_module.check_package_version('pkg')

    assert result.has_update is False
    assert result.error == 'Package not found in Fedora'


def test_check_package_version_connection_error(cuv_module, workdir: Path) -> None:
    """Handles connection errors from Anitya."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    with patch.object(cuv_module, 'query_anitya',
                      side_effect=ConnectionError('timeout')):
        result = cuv_module.check_package_version('pkg')

    assert result.has_update is False
    assert 'timeout' in result.error


def test_check_package_version_no_version(cuv_module, workdir: Path) -> None:
    """Returns error when current version cannot be determined."""
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    result = cuv_module.check_package_version('nonexistent')
    assert result.has_update is False
    assert result.current_version == 'unknown'
    assert 'Could not determine current version' in result.error


def test_check_package_version_prefers_stable(cuv_module, workdir: Path) -> None:
    """Prefers stable_versions[0] over the version field."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '3.0-dev',
        'stable_versions': ['2.0'],
        'id': 10,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    assert result.upstream_version == '2.0'


def test_check_package_version_falls_back_to_version(cuv_module, workdir: Path) -> None:
    """Falls back to version field when stable_versions is empty."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': [],
        'id': 10,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    assert result.upstream_version == '2.0'
    assert result.has_update is True


#
# Tests — check_package_version with project_id / track_upstream
#


def test_check_package_version_uses_project_id_string(cuv_module, workdir: Path) -> None:
    """Uses string project_id from metadata to query Anitya by name."""
    _create_package(workdir, 'golang1.26', '1.26.3',
                    metadata={'version': '1.26.3', 'release': '1',
                              'release_monitoring_project_id': 'golang',
                              'track_upstream': '1.26'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.27.0',
        'stable_versions': ['1.27.0', '1.26.5', '1.26.4', '1.25.10'],
        'id': 100,
    }

    queried_names = []

    def tracking_query(name, distro='Fedora'):
        queried_names.append(name)
        return mock_response

    with patch.object(cuv_module, 'query_anitya', side_effect=tracking_query):
        result = cuv_module.check_package_version('golang1.26')

    # Should query Anitya with 'golang', not 'golang1.26'
    assert queried_names == ['golang']
    # Should filter to track_version 1.26 prefix -> 1.26.5
    assert result.upstream_version == '1.26.5'
    assert result.has_update is True


def test_check_package_version_track_version_filters_stable(cuv_module, workdir: Path) -> None:
    """track_version filters stable_versions to matching prefix."""
    _create_package(workdir, 'pkg', '1.26.3',
                    metadata={'version': '1.26.3', 'release': '1',
                              'track_upstream': '1.26'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0', '1.27.0', '1.26.5'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    assert result.upstream_version == '1.26.5'
    assert result.has_update is True


def test_check_package_version_track_version_no_match(cuv_module, workdir: Path) -> None:
    """Reports no upstream version when no stable versions match track_version."""
    _create_package(workdir, 'pkg', '1.24.0',
                    metadata={'version': '1.24.0', 'release': '1',
                              'track_upstream': '1.24'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0', '1.27.0', '1.26.5'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    # version field (2.0) also doesn't match 1.24 prefix
    assert result.upstream_version is None
    assert result.has_update is False
    assert result.error == "No upstream version reported by Anitya"


def test_check_package_version_track_version_exact_match(cuv_module, workdir: Path) -> None:
    """track_version matches exact version (not just prefix)."""
    _create_package(workdir, 'pkg', '1.26',
                    metadata={'version': '1.26', 'release': '1',
                              'track_upstream': '1.26'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.27',
        'stable_versions': ['1.27', '1.26'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response):
        result = cuv_module.check_package_version('pkg')

    # 1.26 exact match is kept, but it's the same as current so no update
    assert result.upstream_version == '1.26'
    assert result.has_update is False


def test_check_package_version_uses_project_id(cuv_module, workdir: Path) -> None:
    """Uses release_monitoring_project_id to query Anitya by project ID."""
    _create_package(workdir, 'python3.11', '3.11.11',
                    metadata={'version': '3.11.11', 'release': '1',
                              'release_monitoring_project_id': 13254})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['3.11.12', '3.11.11', '3.11.10'],
        'version': '3.11.12',
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response) as mock_query, \
         patch.object(cuv_module, 'query_anitya') as mock_name_query:
        result = cuv_module.check_package_version('python3.11')

    # Should query by project ID, not by name
    mock_query.assert_called_once_with(13254)
    mock_name_query.assert_not_called()
    assert result.upstream_version == '3.11.12'
    assert result.has_update is True


def test_check_package_version_project_id_with_track_version(cuv_module, workdir: Path) -> None:
    """Project ID lookup combined with track_version filtering works."""
    _create_package(workdir, 'python3.11', '3.11.11',
                    metadata={'version': '3.11.11', 'release': '1',
                              'release_monitoring_project_id': 13254,
                              'track_upstream': '3.11'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['3.13.2', '3.12.8', '3.11.12', '3.11.11'],
        'version': '3.13.2',
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('python3.11')

    # track_version should filter to 3.11.x
    assert result.upstream_version == '3.11.12'
    assert result.has_update is True


def test_check_package_version_project_id_int(cuv_module, workdir: Path) -> None:
    """Integer project ID queries Anitya by project ID."""
    _create_package(workdir, 'python3.11', '3.11.11',
                    metadata={'version': '3.11.11', 'release': '1',
                              'release_monitoring_project_id': 13254})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['3.11.12'],
        'version': '3.11.12',
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response) as mock_query, \
         patch.object(cuv_module, 'query_anitya') as mock_name_query:
        result = cuv_module.check_package_version('python3.11')

    # Should use project ID (int), not name-based lookup
    mock_query.assert_called_once_with(13254)
    mock_name_query.assert_not_called()
    assert result.upstream_version == '3.11.12'


def test_check_package_version_project_id_not_found(cuv_module, workdir: Path) -> None:
    """Returns error when project ID lookup fails (404)."""
    _create_package(workdir, 'pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'release_monitoring_project_id': 99999})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      side_effect=ValueError('Project ID 99999 not found on release-monitoring.org')):
        result = cuv_module.check_package_version('pkg')

    assert result.has_update is False
    assert 'Project ID 99999 not found' in result.error


def test_check_package_version_project_id_string_without_track_version(cuv_module, workdir: Path) -> None:
    """String project_id is used for Anitya lookup even without track_version."""
    _create_package(workdir, 'python3.13', '3.13.1',
                    metadata={'version': '3.13.1', 'release': '1',
                              'release_monitoring_project_id': 'python3.13'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '3.13.2',
        'stable_versions': ['3.13.2'],
        'id': 50,
    }

    queried_names = []

    def tracking_query(name, distro='Fedora'):
        queried_names.append(name)
        return mock_response

    with patch.object(cuv_module, 'query_anitya', side_effect=tracking_query):
        result = cuv_module.check_package_version('python3.13')

    assert queried_names == ['python3.13']
    assert result.upstream_version == '3.13.2'
    assert result.has_update is True


def test_check_package_version_strips_version_suffix(cuv_module, workdir: Path) -> None:
    """Strips configured suffix from upstream versions before comparing."""
    _create_package(workdir, 'swift-lang', '6.3.2',
                    metadata={'version': '6.3.2', 'release': '5',
                              'release_monitoring_project_id': 21267,
                              'version_suffix_strip': '-RELEASE'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '6.3.3-RELEASE',
        'stable_versions': ['6.3.3-RELEASE', '6.3.2-RELEASE'],
        'id': 21267,
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('swift-lang')

    assert result.has_update is True
    assert result.upstream_version == '6.3.3'
    assert result.current_version == '6.3.2'


def test_check_package_version_suffix_strip_no_update(cuv_module, workdir: Path) -> None:
    """No update when stripped version matches current."""
    _create_package(workdir, 'swift-lang', '6.3.2',
                    metadata={'version': '6.3.2', 'release': '5',
                              'release_monitoring_project_id': 21267,
                              'version_suffix_strip': '-RELEASE'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '6.3.2-RELEASE',
        'stable_versions': ['6.3.2-RELEASE'],
        'id': 21267,
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('swift-lang')

    assert result.has_update is False
    assert result.upstream_version == '6.3.2'


def test_openjdk_to_rpm(cuv_module) -> None:
    """_openjdk_to_rpm converts upstream tag versions to RPM scheme."""
    fn = cuv_module._openjdk_to_rpm
    assert fn('21.0.12+8') == '21.0.12.0.8'
    assert fn('21.0.12.1+0') == '21.0.12.1.0'
    assert fn('21.0.11+10') == '21.0.11.0.10'
    assert fn('25.0.4+7') == '25.0.4.0.7'
    assert fn('25.0.4.1+0') == '25.0.4.1.0'
    # Leading zeros in build number are stripped
    assert fn('21.0.12+08') == '21.0.12.0.8'

    with pytest.raises(ValueError):
        fn('not-a-version')
    with pytest.raises(ValueError):
        fn('21.0.12')  # missing +build


def test_transform_upstream_version_unknown(cuv_module) -> None:
    """transform_upstream_version raises ValueError for unknown transform."""
    with pytest.raises(ValueError, match="unknown upstream_version_transform"):
        cuv_module.transform_upstream_version('1.0', 'nonexistent_transform')


def test_check_package_version_transforms_openjdk_version(
    cuv_module, workdir: Path,
) -> None:
    """Detects update when Anitya version is transformed to RPM scheme."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.11.0.10',
                    metadata={'version': '21.0.11.0.10', 'release': '2',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['21.0.12.1+0', '21.0.12+8', '21.0.11+10'],
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('java-21-openjdk-portable')

    assert result.has_update is True
    assert result.upstream_version == '21.0.12.1.0'
    assert result.current_version == '21.0.11.0.10'


def test_check_package_version_transform_no_update(
    cuv_module, workdir: Path,
) -> None:
    """No update when transformed version matches current."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.12.0.8',
                    metadata={'version': '21.0.12.0.8', 'release': '1',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['21.0.12+8', '21.0.11+10'],
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('java-21-openjdk-portable')

    assert result.has_update is False
    assert result.upstream_version == '21.0.12.0.8'


def test_check_package_version_transform_with_cpu_release(
    cuv_module, workdir: Path,
) -> None:
    """Handles OpenJDK CPU/PSU releases with 4-part base version."""
    _create_package(workdir, 'java-25-openjdk-portable', '25.0.4.0.7',
                    metadata={'version': '25.0.4.0.7', 'release': '2',
                              'release_monitoring_project_id': 378887,
                              'track_upstream': '25',
                              'upstream_version_transform': 'openjdk_to_rpm'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['25.0.4.1+0', '25.0.4+7'],
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('java-25-openjdk-portable')

    assert result.has_update is True
    assert result.upstream_version == '25.0.4.1.0'


def test_check_package_version_transform_skips_unparseable(
    cuv_module, workdir: Path,
) -> None:
    """Unparseable versions in stable_versions are filtered out."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.11.0.10',
                    metadata={'version': '21.0.11.0.10', 'release': '2',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['bad-tag', '21.0.12+8', '21.0.11+10'],
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response):
        result = cuv_module.check_package_version('java-21-openjdk-portable')

    assert result.has_update is True
    assert result.upstream_version == '21.0.12.0.8'


def test_check_source_exists(cuv_module) -> None:
    """_check_source_exists returns True on 200, False on 404."""

    with patch.object(cuv_module.urllib.request, 'urlopen'):
        assert cuv_module._check_source_exists('https://example.com/a.tar.xz') is True

    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://example.com/a.tar.xz', 404, 'Not Found', Message(), None,
        ),
    ):
        assert cuv_module._check_source_exists('https://example.com/a.tar.xz') is False

    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://example.com/a.tar.xz', 500, 'Server Error', Message(), None,
        ),
    ):
        with pytest.raises(urllib.error.HTTPError):
            cuv_module._check_source_exists('https://example.com/a.tar.xz')


def test_check_source_exists_user_agent_starts_with_mozilla(cuv_module) -> None:
    """_check_source_exists's User-Agent starts with 'Mozilla/5.0'.

    Same rationale as _download_file's identical test: SourceForge (and
    likely other hosts with simplistic bot-detection) 403s any User-Agent
    that doesn't start with 'Mozilla/5.0'. _check_source_exists HEAD-probes
    arbitrary upstream URLs, so it's just as exposed to this as _download_file.
    """
    captured_request = None

    def fake_urlopen(request, timeout=None):
        nonlocal captured_request
        captured_request = request
        return MagicMock()

    with patch.object(cuv_module.urllib.request, 'urlopen', side_effect=fake_urlopen):
        cuv_module._check_source_exists('https://example.com/a.tar.xz')

    assert captured_request is not None
    assert captured_request.get_header('User-agent').startswith('Mozilla/5.0')


def test_check_package_version_skips_unavailable_source(
    cuv_module, workdir: Path,
) -> None:
    """source_availability_check filters out versions whose tarball is not published."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.11.0.10',
                    metadata={'version': '21.0.11.0.10', 'release': '2',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm',
                              'source_availability_check': 'openjdk_osci'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['21.0.12.1+0', '21.0.12+8', '21.0.11+10'],
    }

    def fake_check(version: str, _checker: str, _meta: dict) -> bool:
        return version != '21.0.12.1+0'

    with (
        patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response),
        patch.object(cuv_module, 'check_source_available',
                      side_effect=fake_check),
    ):
        result = cuv_module.check_package_version('java-21-openjdk-portable')

    assert result.has_update is True
    assert result.upstream_version == '21.0.12.0.8'


def test_check_package_version_all_sources_unavailable(
    cuv_module, workdir: Path,
) -> None:
    """No update when all candidate source tarballs return 404."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.11.0.10',
                    metadata={'version': '21.0.11.0.10', 'release': '2',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm',
                              'source_availability_check': 'openjdk_osci'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['21.0.12.1+0'],
        'version': '21.0.12.1+0',
    }

    with (
        patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response),
        patch.object(cuv_module, 'check_source_available', return_value=False),
    ):
        result = cuv_module.check_package_version('java-21-openjdk-portable')

    assert result.has_update is False


def test_check_source_available_unknown(cuv_module) -> None:
    """check_source_available raises ValueError for unknown checker name."""
    with pytest.raises(ValueError, match="unknown source_availability_check"):
        cuv_module.check_source_available('1.0', 'nonexistent', {})


def test_openjdk_source_check_url_construction(cuv_module) -> None:
    """_openjdk_source_check constructs correct URL from version and metadata."""
    checked_urls = []

    def capture_check(url: str) -> bool:
        checked_urls.append(url)
        return True

    with patch.object(cuv_module, '_check_source_exists', side_effect=capture_check):
        cuv_module._openjdk_source_check('21.0.12+8', {'track_upstream': '21'})

    assert checked_urls == [
        'https://openjdk-sources.osci.io/openjdk21/openjdk-21.0.12+8.tar.xz',
    ]

    checked_urls.clear()
    with patch.object(cuv_module, '_check_source_exists', side_effect=capture_check):
        cuv_module._openjdk_source_check('25.0.1+3', {'track_upstream': 'latest'})

    assert checked_urls == [
        'https://openjdk-sources.osci.io/openjdk/openjdk-25.0.1+3.tar.xz',
    ]

    checked_urls.clear()
    with patch.object(cuv_module, '_check_source_exists', side_effect=capture_check):
        cuv_module._openjdk_source_check('25.0.1+3', {})

    assert checked_urls == [
        'https://openjdk-sources.osci.io/openjdk/openjdk-25.0.1+3.tar.xz',
    ]


def test_check_package_version_source_checker_dispatches_correctly(
    cuv_module, workdir: Path,
) -> None:
    """source_availability_check dispatches to the named checker with correct args."""
    _create_package(workdir, 'java-21-openjdk-portable', '21.0.11.0.10',
                    metadata={'version': '21.0.11.0.10', 'release': '2',
                              'release_monitoring_project_id': 369281,
                              'track_upstream': '21',
                              'upstream_version_transform': 'openjdk_to_rpm',
                              'source_availability_check': 'openjdk_osci'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'stable_versions': ['21.0.12+8'],
    }

    checked_urls = []

    def capture_check(url: str) -> bool:
        checked_urls.append(url)
        return True

    with (
        patch.object(cuv_module, 'query_anitya_by_project_id',
                      return_value=mock_response),
        patch.object(cuv_module, '_check_source_exists',
                      side_effect=capture_check),
    ):
        cuv_module.check_package_version('java-21-openjdk-portable')

    assert checked_urls == [
        'https://openjdk-sources.osci.io/openjdk21/openjdk-21.0.12+8.tar.xz',
    ]


def test_matches_track_version(cuv_module) -> None:
    """_matches_track_version correctly handles prefix matching."""
    matches = cuv_module._matches_track_version
    assert matches('1.26', '1.26') is True
    assert matches('1.26.0', '1.26') is True
    assert matches('1.26.3', '1.26') is True
    assert matches('1.27.0', '1.26') is False
    assert matches('1.260', '1.26') is False
    assert matches('1.3', '1.26') is False
    assert matches('2.0', '1.26') is False


#
# Tests — get_all_packages
#


def test_get_all_packages(cuv_module, workdir: Path) -> None:
    """Lists package directories that contain spec files."""
    _create_package(workdir, 'alpha', '1.0')
    _create_package(workdir, 'beta', '2.0')

    # A directory without a spec file should not be listed
    (workdir / 'rpms' / 'empty').mkdir()

    cuv_module.RPMS_DIR = workdir / 'rpms'
    packages = cuv_module.get_all_packages()

    assert packages == ['alpha', 'beta']


def test_get_all_packages_ignores_hidden(cuv_module, workdir: Path) -> None:
    """Ignores hidden directories."""
    _create_package(workdir, 'visible', '1.0')
    hidden_dir = workdir / 'rpms' / '.hidden'
    hidden_dir.mkdir()
    (hidden_dir / 'hidden.spec').write_text('Name: hidden\nVersion: 1\n')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    packages = cuv_module.get_all_packages()
    assert packages == ['visible']


#
# Tests — download_new_sources
#


def test_get_spec_source_urls_includes_lua_generated_source(
    cuv_module, tmp_path: Path
) -> None:
    """Reads Source tags emitted by Lua during RPM parsing."""
    spec = tmp_path / 'dynamic.spec'
    spec.write_text(r'''Version: 1.0
%{lua:
print("Source7: https://example.com/generated-1.0.tar.gz\n")
}
Name: dynamic
Release: 1
Summary: Dynamic source test
License: MIT
Source99: local.conf

%description
Dynamic source test

%files
''')

    sources = cuv_module._get_spec_source_urls(str(spec), str(tmp_path))

    assert sources == {
        7: 'https://example.com/generated-1.0.tar.gz',
    }


def test_get_spec_source_urls_includes_sourcelist(
    cuv_module, tmp_path: Path
) -> None:
    """Preserves support for sources declared in a sourcelist section."""
    spec = tmp_path / 'sourcelist.spec'
    spec.write_text('''Name: sourcelist-test
Version: 1.0
Release: 1
Summary: Sourcelist test
License: MIT

%sourcelist
https://example.com/sourcelist-test-1.0.tar.gz

%description
Sourcelist test

%files
''')

    sources = cuv_module._get_spec_source_urls(str(spec), str(tmp_path))

    assert sources == {
        0: 'https://example.com/sourcelist-test-1.0.tar.gz',
    }


def test_download_new_sources(cuv_module, workdir: Path) -> None:
    """Downloads new source, updates sources file, uploads to lookaside."""
    pkg_dir = _create_package(
        workdir, 'pkg', '2.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
    )

    with patch.object(cuv_module, '_download_file') as mock_dl, \
         patch.object(cuv_module, '_upload_to_lookaside') as mock_ul, \
         patch.object(cuv_module, '_compute_file_hash', return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={0: 'https://example.com/pkg/pkg-2.0.tar.gz'}):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']
    mock_dl.assert_called_once()
    mock_ul.assert_called_once()

    # sources file should be updated
    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    assert len(entries) == 1
    assert entries[0]['filename'] == 'pkg-2.0.tar.gz'
    assert entries[0]['hash'] == 'newhash'


def test_download_new_sources_download_failure(cuv_module, workdir: Path) -> None:
    """Crashes with exception when download fails."""
    _create_package(
        workdir, 'pkg', '2.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
    )

    with patch.object(cuv_module, '_download_file',
                      side_effect=Exception('network error')), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={0: 'https://example.com/pkg/pkg-2.0.tar.gz'}):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        with pytest.raises(Exception, match='network error'):
            cuv_module.download_new_sources('pkg', '1.0', '2.0')


def test_download_file_user_agent_starts_with_mozilla(
    cuv_module, tmp_path: Path
) -> None:
    """_download_file's User-Agent starts with 'Mozilla/5.0'.

    SourceForge (and likely other hosts with simplistic bot-detection) 403s
    any User-Agent that doesn't start with 'Mozilla/5.0', confirmed directly
    against sourceforge.net. Regression test for that specific requirement --
    an identifying-but-not-Mozilla-prefixed UA (e.g. the previous
    'hummingbird-rpms-version-checker/1.0') silently breaks downloads from
    such hosts.
    """
    captured_request = None

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b''

    def fake_urlopen(request, timeout=None):
        nonlocal captured_request
        captured_request = request
        return FakeResponse()

    with patch.object(cuv_module.urllib.request, 'urlopen', side_effect=fake_urlopen):
        cuv_module._download_file('https://example.com/file.tar.gz', tmp_path / 'file.tar.gz')

    assert captured_request is not None
    assert captured_request.get_header('User-agent').startswith('Mozilla/5.0')


def test_download_new_sources_refuses_empty_source_discovery(
    cuv_module, workdir: Path
) -> None:
    """Does not erase sources when source discovery unexpectedly returns nothing."""
    pkg_dir = _create_package(
        workdir, 'pkg', '2.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
    )
    original_sources = (pkg_dir / 'sources').read_text()

    with patch.object(cuv_module, '_get_spec_source_urls', return_value={}), \
         patch.object(cuv_module, '_get_spec_sources', return_value={}):
        cuv_module.RPMS_DIR = workdir / 'rpms'
        with pytest.raises(RuntimeError, match='refusing to remove 1 existing'):
            cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert (pkg_dir / 'sources').read_text() == original_sources


def test_download_new_sources_allows_local_only_sources(
    cuv_module, workdir: Path
) -> None:
    """Preserves lookaside entries declared as local-only spec sources."""
    pkg_dir = _create_package(
        workdir, 'pkg', '2.0',
        sources={'local-data.tar.gz': 'oldhash'},
    )
    (pkg_dir / 'pkg.spec').write_text('''Name: pkg
Version: 2.0
Release: 1
Summary: Local source test
License: MIT
Source0: local-data.tar.gz

%description
Local source test

%files
''')
    original_sources = (pkg_dir / 'sources').read_text()

    cuv_module.RPMS_DIR = workdir / 'rpms'
    downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert downloaded == []
    assert (pkg_dir / 'sources').read_text() == original_sources


def test_download_new_sources_no_version_in_filename(cuv_module, workdir: Path) -> None:
    """Skips sources whose filename already appears in the sources file."""
    _create_package(
        workdir, 'pkg', '2.0',
        sources={'static-data.tar.gz': 'somehash'},
    )

    with patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={0: 'https://example.com/static-data.tar.gz'}):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert downloaded == []


def test_download_new_sources_independent_version_bump(
    cuv_module, workdir: Path
) -> None:
    """Downloads sources whose version changed independently of the main package."""
    pkg_dir = _create_package(
        workdir, 'pkg', '2.0',
        sources={
            'pkg-1.0.tar.gz': 'oldhash_main',
            'dep-3.0.tar.gz': 'oldhash_dep',
        },
    )

    with patch.object(cuv_module, '_download_file') as mock_dl, \
         patch.object(cuv_module, '_upload_to_lookaside') as mock_ul, \
         patch.object(cuv_module, '_compute_file_hash', return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={
                          0: 'https://example.com/pkg-2.0.tar.gz',
                          1: 'https://example.com/dep-4.0.tar.gz',
                      }):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert sorted(downloaded) == ['dep-4.0.tar.gz', 'pkg-2.0.tar.gz']
    assert mock_dl.call_count == 2
    assert mock_ul.call_count == 2

    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    filenames = {e['filename'] for e in entries}
    assert filenames == {'pkg-2.0.tar.gz', 'dep-4.0.tar.gz'}


def test_download_new_sources_removes_stale_entries(
    cuv_module, workdir: Path
) -> None:
    """Removes sources entries that no longer appear in the spec."""
    pkg_dir = _create_package(
        workdir, 'pkg', '2.0',
        sources={
            'pkg-1.0.tar.gz': 'oldhash',
            'removed-dep-1.0.tar.gz': 'oldhash_removed',
        },
    )

    with patch.object(cuv_module, '_download_file'), \
         patch.object(cuv_module, '_upload_to_lookaside'), \
         patch.object(cuv_module, '_compute_file_hash', return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={
                          0: 'https://example.com/pkg-2.0.tar.gz',
                      }):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']

    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    filenames = {e['filename'] for e in entries}
    assert 'removed-dep-1.0.tar.gz' not in filenames
    assert 'pkg-2.0.tar.gz' in filenames


#
# Tests — _regenerate_vendor_archive / go-vendor-tools
#


def test_download_new_sources_regenerates_vendor_archive(
    cuv_module, workdir: Path
) -> None:
    """Regenerates vendor archive for packages with go-vendor-tools.toml."""
    pkg_dir = _create_package(
        workdir, 'gopkg', '2.0',
        sources={
            'gopkg-1.0.tar.gz': 'oldhash_src',
            'gopkg-1.0-vendor.tar.bz2': 'oldhash_vendor',
        },
    )
    # Create go-vendor-tools.toml to trigger vendor regeneration
    (pkg_dir / 'go-vendor-tools.toml').write_text('[archive]\n')

    def fake_subprocess_run(cmd, **kwargs):
        """Simulate go_vendor_archive creating the output file."""
        if cmd[0] == 'go_vendor_archive':
            assert '-O' not in cmd, "-O must not be passed with a specfile"
            # The tool derives the output name from the spec; simulate
            # by creating the expected vendor archive in cwd
            cwd = Path(kwargs.get('cwd', '.'))
            (cwd / 'gopkg-2.0-vendor.tar.bz2').write_bytes(b'fake vendor archive')
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    with patch.object(cuv_module, '_download_file'), \
         patch.object(cuv_module, '_upload_to_lookaside') as mock_ul, \
         patch.object(cuv_module, '_compute_file_hash',
                      return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={
                          0: 'https://example.com/gopkg/gopkg-2.0.tar.gz',
                      }), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('gopkg', '1.0', '2.0')

    # Both the URL source and vendor archive should be in the result
    assert 'gopkg-2.0.tar.gz' in downloaded
    assert 'gopkg-2.0-vendor.tar.bz2' in downloaded

    # Upload should be called twice: once for the URL source, once for vendor
    assert mock_ul.call_count == 2

    # sources file should be updated with both entries
    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    filenames = {e['filename'] for e in entries}
    assert 'gopkg-2.0.tar.gz' in filenames
    assert 'gopkg-2.0-vendor.tar.bz2' in filenames
    # Old filenames should be gone
    assert 'gopkg-1.0.tar.gz' not in filenames
    assert 'gopkg-1.0-vendor.tar.bz2' not in filenames


def test_download_new_sources_no_vendor_without_toml(
    cuv_module, workdir: Path
) -> None:
    """Does not regenerate vendor archive when go-vendor-tools.toml is absent."""
    _create_package(
        workdir, 'pkg', '2.0',
        sources={
            'pkg-1.0.tar.gz': 'oldhash_src',
            'pkg-1.0-vendor.tar.bz2': 'oldhash_vendor',
        },
    )
    # No go-vendor-tools.toml

    with patch.object(cuv_module, '_download_file'), \
         patch.object(cuv_module, '_upload_to_lookaside'), \
         patch.object(cuv_module, '_compute_file_hash',
                      return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={
                          0: 'https://example.com/pkg/pkg-2.0.tar.gz',
                      }):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    # Only the URL-based source should be downloaded, vendor left untouched
    assert downloaded == ['pkg-2.0.tar.gz']


def test_regenerate_vendor_archive_failure(cuv_module, workdir: Path) -> None:
    """Raises RuntimeError when go_vendor_archive fails."""
    pkg_dir = _create_package(
        workdir, 'gopkg', '2.0',
        sources={
            'gopkg-1.0.tar.gz': 'oldhash_src',
            'gopkg-1.0-vendor.tar.bz2': 'oldhash_vendor',
        },
    )
    (pkg_dir / 'go-vendor-tools.toml').write_text('[archive]\n')

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] == 'go_vendor_archive':
            return subprocess.CompletedProcess(
                cmd, 1, stdout='', stderr='vendor creation failed'
            )
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    with patch.object(cuv_module, '_download_file'), \
         patch.object(cuv_module, '_upload_to_lookaside'), \
         patch.object(cuv_module, '_compute_file_hash',
                      return_value='newhash'), \
         patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={
                          0: 'https://example.com/gopkg/gopkg-2.0.tar.gz',
                      }), \
         patch('subprocess.run', side_effect=fake_subprocess_run):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        with pytest.raises(RuntimeError, match='vendor creation failed'):
            cuv_module.download_new_sources('gopkg', '1.0', '2.0')


#
# Tests — _load_source_pipeline / _run_gorget_pipeline
#


def test_load_source_pipeline_missing(cuv_module, workdir: Path) -> None:
    """Returns None when no source-pipeline.yaml exists."""
    cuv_module.METADATA_DIR = workdir / 'metadata'
    assert cuv_module._load_source_pipeline('pkg') is None


def test_load_source_pipeline_exists(cuv_module, workdir: Path) -> None:
    """Returns the path when source-pipeline.yaml exists."""
    cuv_module.METADATA_DIR = workdir / 'metadata'
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    assert cuv_module._load_source_pipeline('pkg') == pipeline_file


def _output_dir_from_gorget_cmd(cmd: list[str]) -> Path:
    """Extract the --output-dir value from a gorget argv list."""
    return Path(cmd[cmd.index('--output-dir') + 1])


def _fake_gorget_run(artifacts: dict[str, bytes]):
    """Build a `subprocess.run` side_effect simulating a successful gorget
    run: writes `artifacts` plus a `sources` manifest into the
    --output-dir directory."""

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] != 'gorget':
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

        output_dir = _output_dir_from_gorget_cmd(cmd)
        lines = []
        for i, (filename, content) in enumerate(artifacts.items()):
            (output_dir / filename).write_bytes(content)
            lines.append(f"SHA512 ({filename}) = fakehash{i}")
        (output_dir / 'sources').write_text('\n'.join(lines) + '\n')
        (output_dir / 'report.json').write_text('{}')
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    return fake_subprocess_run


def test_run_gorget_pipeline_success(cuv_module, workdir: Path) -> None:
    """Runs gorget natively, uploads emitted artifacts, replaces sources."""
    pkg_dir = _create_package(
        workdir, 'pkg', '1.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
    )
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    fake_run = _fake_gorget_run({
        'pkg-2.0.tar.gz': b'source',
        'pkg-2.0-vendor.tar.gz': b'vendor',
    })

    with patch.object(cuv_module, '_upload_to_lookaside') as mock_ul, \
         patch('subprocess.run', side_effect=fake_run):
        cuv_module.RPMS_DIR = workdir / 'rpms'
        cuv_module.METADATA_DIR = workdir / 'metadata'
        downloaded = cuv_module._run_gorget_pipeline(
            'pkg', '1.0', '2.0', pipeline_file,
        )

    assert sorted(downloaded) == ['pkg-2.0-vendor.tar.gz', 'pkg-2.0.tar.gz']
    assert mock_ul.call_args_list == [
        call(pkg_dir / 'pkg-2.0.tar.gz', 'pkg', 'SHA512'),
        call(pkg_dir / 'pkg-2.0-vendor.tar.gz', 'pkg', 'SHA512'),
    ]
    assert (pkg_dir / 'pkg-2.0.tar.gz').read_bytes() == b'source'
    assert (pkg_dir / 'pkg-2.0-vendor.tar.gz').read_bytes() == b'vendor'

    # Old file removed, sources file replaced wholesale (not hand-patched)
    assert not (pkg_dir / 'pkg-1.0.tar.gz').exists()
    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    filenames = {e['filename'] for e in entries}
    assert filenames == {'pkg-2.0.tar.gz', 'pkg-2.0-vendor.tar.gz'}


def test_run_gorget_pipeline_always_passes_debug(cuv_module, workdir: Path) -> None:
    """--debug is always passed, so gorget's stage/step trace lands in the
    job log."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    fake_run = _fake_gorget_run({'pkg-2.0.tar.gz': b'x'})
    with patch.object(cuv_module, '_upload_to_lookaside'), \
         patch('subprocess.run', side_effect=fake_run) as mock_run:
        cuv_module.RPMS_DIR = workdir / 'rpms'
        cuv_module.METADATA_DIR = workdir / 'metadata'
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    cmd = mock_run.call_args.args[0]
    assert '--debug' in cmd


def test_run_gorget_pipeline_passes_upstream_repo_when_declared(
    cuv_module, workdir: Path
) -> None:
    """--upstream-repo is passed when metadata/<package>.json declares one,
    so the pipeline can reference ${UPSTREAM_REPO} instead of duplicating
    the URL."""
    _create_package(
        workdir, 'pkg', '1.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
        metadata={'upstream_repo': 'https://example.com/org/pkg'},
    )
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    fake_run = _fake_gorget_run({'pkg-2.0.tar.gz': b'x'})
    with patch.object(cuv_module, '_upload_to_lookaside'), \
         patch('subprocess.run', side_effect=fake_run) as mock_run:
        cuv_module.RPMS_DIR = workdir / 'rpms'
        cuv_module.METADATA_DIR = workdir / 'metadata'
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    cmd = mock_run.call_args.args[0]
    assert cmd[cmd.index('--upstream-repo') + 1] == 'https://example.com/org/pkg'


def test_run_gorget_pipeline_omits_upstream_repo_when_not_declared(
    cuv_module, workdir: Path
) -> None:
    """No --upstream-repo when metadata/<package>.json has no upstream_repo
    (or doesn't exist at all)."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    fake_run = _fake_gorget_run({'pkg-2.0.tar.gz': b'x'})
    with patch.object(cuv_module, '_upload_to_lookaside'), \
         patch('subprocess.run', side_effect=fake_run) as mock_run:
        cuv_module.RPMS_DIR = workdir / 'rpms'
        cuv_module.METADATA_DIR = workdir / 'metadata'
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    cmd = mock_run.call_args.args[0]
    assert '--upstream-repo' not in cmd


def test_run_gorget_pipeline_logs_command_and_duration(
    cuv_module, workdir: Path, caplog
) -> None:
    """Pipeline command and elapsed time are visible in CI logs, matching
    run_git/_run_hook's diagnostics -- and stderr is left unbuffered rather
    than captured, so a chatty gorget invocation can't stall on a full pipe."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    fake_run = _fake_gorget_run({'pkg-2.0.tar.gz': b'x'})

    with patch.object(cuv_module, '_upload_to_lookaside'), \
         patch('subprocess.run', side_effect=fake_run), \
         patch.object(cuv_module.time, 'monotonic', side_effect=[10.0, 12.5]), \
         caplog.at_level('INFO'):
        cuv_module.RPMS_DIR = workdir / 'rpms'
        cuv_module.METADATA_DIR = workdir / 'metadata'
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    assert 'running gorget pipeline 1.0 -> 2.0: gorget --version 2.0' in caplog.text
    assert 'gorget pipeline completed after 2.5s' in caplog.text


def test_run_gorget_pipeline_creates_gpg_keys_dir(
    cuv_module, workdir: Path
) -> None:
    """Creates metadata/gpg-keys/ if it doesn't already exist, for --gpg-keys-dir."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with patch('subprocess.run', side_effect=_fake_gorget_run({'pkg-2.0.tar.gz': b'x'})):
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    assert (workdir / 'metadata' / 'gpg-keys').is_dir()


def test_run_gorget_pipeline_failure_includes_report(
    cuv_module, workdir: Path
) -> None:
    """Raises RuntimeError with the report.json contents on non-zero exit."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] != 'gorget':
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        output_dir = _output_dir_from_gorget_cmd(cmd)
        (output_dir / 'report.json').write_text('{"stages": ["verify failed"]}')
        return subprocess.CompletedProcess(
            cmd, 1, stdout='republication check failed',
        )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with patch('subprocess.run', side_effect=fake_subprocess_run), \
         pytest.raises(RuntimeError, match='republication check failed'):
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)


def test_run_gorget_pipeline_missing_sources_file(
    cuv_module, workdir: Path
) -> None:
    """Raises RuntimeError if gorget exits 0 but writes no sources file."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    def fake_subprocess_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with patch('subprocess.run', side_effect=fake_subprocess_run), \
         pytest.raises(RuntimeError, match="emitted no 'sources' file"):
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)


def test_run_gorget_pipeline_empty_sources_file(
    cuv_module, workdir: Path
) -> None:
    """Raises RuntimeError if gorget exits 0 but writes an empty sources
    file, instead of silently deleting every existing source artifact."""
    pkg_dir = _create_package(
        workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'},
    )
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] != 'gorget':
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        output_dir = _output_dir_from_gorget_cmd(cmd)
        (output_dir / 'sources').write_text('')
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with patch('subprocess.run', side_effect=fake_subprocess_run), \
         pytest.raises(RuntimeError, match='empty .sources. file'):
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)

    # Old sources manifest must survive -- the guard must fire before any
    # stale-file cleanup or sources-file rewrite happens.
    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    assert {e['filename'] for e in entries} == {'pkg-1.0.tar.gz'}


def test_run_gorget_pipeline_missing_declared_artifact(
    cuv_module, workdir: Path
) -> None:
    """Raises RuntimeError if the emitted sources file references a file
    that wasn't actually written to /output."""
    _create_package(workdir, 'pkg', '1.0', sources={'pkg-1.0.tar.gz': 'oldhash'})
    pipeline_file = workdir / 'metadata' / 'pkg.source-pipeline.yaml'
    pipeline_file.write_text('fetch: []\n')

    def fake_subprocess_run(cmd, **kwargs):
        if cmd[0] != 'gorget':
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        output_dir = _output_dir_from_gorget_cmd(cmd)
        # sources references a file that was never written
        (output_dir / 'sources').write_text(
            'SHA512 (pkg-2.0.tar.gz) = deadbeef\n'
        )
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with patch('subprocess.run', side_effect=fake_subprocess_run), \
         pytest.raises(RuntimeError, match='was not emitted to /output'):
        cuv_module._run_gorget_pipeline('pkg', '1.0', '2.0', pipeline_file)


#
# Tests — update_spec_version
#


def test_update_spec_version(cuv_module, workdir: Path) -> None:
    """Updates spec version, downloads sources, marks modified."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={
                        'version': '1.0',
                        'release': '1',
                        'source': 'https://example.com',
                    })

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources',
                      return_value=['pkg-2.0.tar.gz']) as mock_dl:
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']
    mock_dl.assert_called_once_with('pkg', '1.0', '2.0')

    # Metadata should be marked as modified with updated version. The spec's
    # synthetic 0.1 is not persisted as a Fedora release.
    with open(workdir / 'metadata' / 'pkg.json') as f:
        data = json.load(f)
    assert data['modification_status'] == 'modified'
    assert 'Update to upstream version 2.0' in data['modification_reason']
    assert data['version'] == '2.0'
    assert 'release' not in data


def test_update_spec_version_sets_release_0_1(cuv_module, workdir: Path) -> None:
    """Release is set to 0.1 so that a later Fedora import (Release >= 1)
    sorts higher and replaces the locally-built version."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources', return_value=[]):
        cuv_module.update_spec_version('pkg', '2.0')

    spec_content = (workdir / 'rpms' / 'pkg' / 'pkg.spec').read_text()
    assert 'Release: 0.1%{?dist}' in spec_content


def test_update_spec_version_removes_independent_release(cuv_module, workdir: Path) -> None:
    """Independent packages do not retain a local metadata release."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={
                        'version': '1.0',
                        'release': '1',
                        'modification_status': 'independent',
                    })
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources', return_value=[]):
        cuv_module.update_spec_version('pkg', '2.0')

    data = json.loads((workdir / 'metadata' / 'pkg.json').read_text())
    assert data['modification_status'] == 'independent'
    assert 'release' not in data


def test_update_spec_version_ignores_matching_dependency_version(
    cuv_module, workdir: Path,
) -> None:
    """A literal Version update does not scan or rewrite other matching tags."""
    package_dir = _create_package(
        workdir, 'pkg', '13.1.0',
        sources={'pkg-13.1.0.tar.gz': 'oldhash'},
        metadata={'version': '13.1.0', 'release': '1'},
    )
    spec_file = package_dir / 'pkg.spec'
    spec_file.write_text(spec_file.read_text().replace(
        '%description',
        'Provides: bundled(npm(commander)) = 13.1.0\n\n%description',
    ))
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources', return_value=[]):
        cuv_module.update_spec_version('pkg', '13.1.1')

    updated_spec = spec_file.read_text()
    assert 'Version: 13.1.1' in updated_spec
    assert 'bundled(npm(commander)) = 13.1.0' in updated_spec


#
# Tests — _load_update_hooks
#


def test_load_update_hooks_no_file(cuv_module, workdir: Path) -> None:
    """Returns None when no update-hooks.yaml exists."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    assert cuv_module._load_update_hooks('pkg') is None


def test_load_update_hooks_all_phases(cuv_module, workdir: Path) -> None:
    """All three phases are populated from the YAML file."""
    import yaml
    _create_package(workdir, 'pkg', '1.0')
    hooks_data = {
        'update_spec': 'echo spec',
        'download_sources': 'echo src',
        'post_update': 'echo post',
    }
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    hooks = cuv_module._load_update_hooks('pkg')
    assert hooks is not None
    assert hooks.update_spec == 'echo spec'
    assert hooks.download_sources == 'echo src'
    assert hooks.post_update == 'echo post'


def test_load_update_hooks_partial(cuv_module, workdir: Path) -> None:
    """Only post_update set; other fields are None."""
    import yaml
    _create_package(workdir, 'pkg', '1.0')
    hooks_data = {'post_update': 'echo done'}
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    hooks = cuv_module._load_update_hooks('pkg')
    assert hooks is not None
    assert hooks.update_spec is None
    assert hooks.download_sources is None
    assert hooks.post_update == 'echo done'


def test_load_update_hooks_unknown_key(cuv_module, workdir: Path) -> None:
    """Unknown phase key raises ValueError."""
    import yaml
    _create_package(workdir, 'pkg', '1.0')
    hooks_data = {'post_update': 'echo ok', 'pre_build': 'echo bad'}
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    with pytest.raises(ValueError, match='pre_build'):
        cuv_module._load_update_hooks('pkg')


#
# Tests — _build_hook_env
#


def test_build_hook_env(cuv_module, workdir: Path) -> None:
    """Environment dict has all expected UPDATE_* variables."""
    _create_package(workdir, 'mypkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.ROOT_DIR = workdir

    env = cuv_module._build_hook_env('mypkg', '1.0', '2.0')
    assert env['UPDATE_PACKAGE'] == 'mypkg'
    assert env['UPDATE_OLD_VERSION'] == '1.0'
    assert env['UPDATE_NEW_VERSION'] == '2.0'
    assert env['UPDATE_SPEC_FILE'].endswith('mypkg.spec')
    assert env['UPDATE_PACKAGE_DIR'] == str(workdir / 'rpms' / 'mypkg')
    assert env['UPDATE_SOURCES_FILE'].endswith('sources')
    assert env['UPDATE_ROOT_DIR'] == str(workdir)


#
# Tests — _run_hook
#


def test_run_hook_success(cuv_module, workdir: Path) -> None:
    """Successful command returns CompletedProcess with stdout."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    import os
    env = {**os.environ, 'UPDATE_PACKAGE': 'pkg'}

    result = cuv_module._run_hook('test', 'echo hello', 'pkg', env)
    assert result.returncode == 0
    assert 'hello' in result.stdout


def test_run_hook_failure(cuv_module, workdir: Path) -> None:
    """Failing command raises RuntimeError."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    import os
    env = {**os.environ, 'UPDATE_PACKAGE': 'pkg'}

    with pytest.raises(RuntimeError, match='test hook failed'):
        cuv_module._run_hook('test', 'exit 1', 'pkg', env)


def test_run_hook_receives_env(cuv_module, workdir: Path) -> None:
    """Hook command can read the UPDATE_* environment variables."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    import os
    env = {**os.environ, 'UPDATE_NEW_VERSION': '9.9.9'}

    result = cuv_module._run_hook(
        'test', 'echo "$UPDATE_NEW_VERSION"', 'pkg', env,
    )
    assert '9.9.9' in result.stdout


def test_run_hook_streams_stderr(cuv_module, workdir: Path, capfd) -> None:
    """Hook diagnostics on stderr are emitted while stdout remains captured."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    import os
    env = {**os.environ, 'UPDATE_PACKAGE': 'pkg'}

    result = cuv_module._run_hook(
        'test', 'echo source.tar.gz; echo progress >&2', 'pkg', env,
    )

    assert result.stdout.strip() == 'source.tar.gz'
    assert 'progress' in capfd.readouterr().err


#
# Tests — update_spec_version with hooks
#


def test_update_spec_version_with_update_spec_hook(
    cuv_module, workdir: Path,
) -> None:
    """update_spec hook replaces default version update logic."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})

    import yaml
    hooks_data = {
        'update_spec': 'sed -i "s/Version: 1.0/Version: 2.0/" "${UPDATE_SPEC_FILE}"',
    }
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources',
                      return_value=['pkg-2.0.tar.gz']):
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']
    spec_content = (workdir / 'rpms' / 'pkg' / 'pkg.spec').read_text()
    assert 'Version: 2.0' in spec_content


def test_update_spec_version_with_download_sources_hook(
    cuv_module, workdir: Path,
) -> None:
    """download_sources hook replaces default download path."""
    pkg_dir = _create_package(workdir, 'pkg', '1.0',
                              sources={'pkg-1.0.tar.gz': 'oldhash'},
                              metadata={'version': '1.0', 'release': '1'})

    import yaml
    # The hook creates a fake file and prints its name
    hooks_data = {
        'download_sources': (
            'echo "fake data" > pkg-2.0.tar.gz\n'
            'echo pkg-2.0.tar.gz\n'
        ),
    }
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, '_upload_to_lookaside'):
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    assert 'pkg-2.0.tar.gz' in downloaded
    # sources file should replace old entry, not append
    entries = cuv_module._parse_sources_file(pkg_dir / 'sources')
    filenames = {e['filename'] for e in entries}
    assert 'pkg-2.0.tar.gz' in filenames
    assert 'pkg-1.0.tar.gz' not in filenames


def test_update_spec_version_with_post_update_hook(
    cuv_module, workdir: Path, caplog,
) -> None:
    """post_update hook runs after default phases."""
    pkg_dir = _create_package(workdir, 'pkg', '1.0',
                              sources={'pkg-1.0.tar.gz': 'oldhash'},
                              metadata={'version': '1.0', 'release': '1'})

    import yaml
    hooks_data = {
        'post_update': 'echo "post-hook ran" > post-hook-marker',
    }
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources', return_value=[]), \
         caplog.at_level('INFO'):
        cuv_module.update_spec_version('pkg', '2.0')

    # The hook should have created a marker file
    marker = pkg_dir / 'post-hook-marker'
    assert marker.exists()
    assert 'post-hook ran' in marker.read_text()
    assert 'pkg: running post_update hook' in caplog.text
    assert 'pkg: post_update hook completed after' in caplog.text


def test_update_spec_version_hook_failure_propagates(
    cuv_module, workdir: Path,
) -> None:
    """Hook failure propagates RuntimeError to caller."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})

    import yaml
    hooks_data = {
        'update_spec': 'exit 42',
    }
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump(hooks_data)
    )

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with pytest.raises(RuntimeError, match='update_spec hook failed'):
        cuv_module.update_spec_version('pkg', '2.0')


def test_update_spec_version_no_hooks_default_path(
    cuv_module, workdir: Path,
) -> None:
    """Without hooks file, the default specfile-library path is used."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, 'download_new_sources',
                      return_value=['pkg-2.0.tar.gz']) as mock_dl:
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']
    mock_dl.assert_called_once_with('pkg', '1.0', '2.0')

    spec_content = (workdir / 'rpms' / 'pkg' / 'pkg.spec').read_text()
    assert 'Version: 2.0' in spec_content
    assert 'Release: 0.1%{?dist}' in spec_content


def test_update_spec_version_pipeline_takes_priority_over_hooks(
    cuv_module, workdir: Path,
) -> None:
    """A source-pipeline.yaml takes priority over update-hooks.yaml and the
    generic default for the source-download phase."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})

    import yaml
    (workdir / 'metadata' / 'pkg.update-hooks.yaml').write_text(
        yaml.dump({'download_sources': 'echo should-not-run.tar.gz'})
    )
    (workdir / 'metadata' / 'pkg.source-pipeline.yaml').write_text('fetch: []\n')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    with patch.object(cuv_module, '_run_gorget_pipeline',
                      return_value=['pkg-2.0.tar.gz']) as mock_pipeline:
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    assert downloaded == ['pkg-2.0.tar.gz']
    mock_pipeline.assert_called_once_with(
        'pkg', '1.0', '2.0', workdir / 'metadata' / 'pkg.source-pipeline.yaml',
    )


def test_update_spec_version_skip_pipeline_forces_legacy_path(
    cuv_module, workdir: Path,
) -> None:
    """The skip_pipeline global (--skip-pipeline) bypasses source-pipeline.yaml
    even when it exists, falling back to the default path."""
    _create_package(workdir, 'pkg', '1.0',
                    sources={'pkg-1.0.tar.gz': 'oldhash'},
                    metadata={'version': '1.0', 'release': '1'})
    (workdir / 'metadata' / 'pkg.source-pipeline.yaml').write_text('fetch: []\n')

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir
    cuv_module.skip_pipeline = True

    try:
        with patch.object(cuv_module, '_run_gorget_pipeline') as mock_pipeline, \
             patch.object(cuv_module, 'download_new_sources',
                          return_value=['pkg-2.0.tar.gz']) as mock_dl:
            downloaded = cuv_module.update_spec_version('pkg', '2.0')
    finally:
        cuv_module.skip_pipeline = False

    mock_pipeline.assert_not_called()
    mock_dl.assert_called_once_with('pkg', '1.0', '2.0')
    assert downloaded == ['pkg-2.0.tar.gz']


#
# Tests — git commit excludes lookaside files
#


def test_commit_excludes_lookaside_files(cuv_module, workdir: Path) -> None:
    """Downloaded sources are added to .gitignore and excluded from commits."""
    pkg_dir = _create_package(
        workdir, 'pkg', '1.0',
        sources={'pkg-1.0.tar.gz': 'oldhash'},
        metadata={'version': '1.0', 'release': '1'},
    )

    # Stage initial package state
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add pkg'], cwd=workdir, check=True)

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.ROOT_DIR = workdir

    # Simulate what main() does: update spec, create source file,
    # update .gitignore, git add, commit.
    with patch.object(cuv_module, 'download_new_sources',
                      return_value=['pkg-2.0.tar.gz']):
        downloaded = cuv_module.update_spec_version('pkg', '2.0')

    # Create the fake downloaded file (simulating the download)
    (pkg_dir / 'pkg-2.0.tar.gz').write_bytes(b'fake tarball')

    # Replicate the logic from main(): update .gitignore, then git add
    cuv_module._update_gitignore(pkg_dir, downloaded)

    cuv_module.run_git('add', 'rpms/pkg', 'metadata/pkg.json', cwd=workdir)

    result = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        cwd=workdir, capture_output=True, text=True, check=True,
    )
    staged_files = result.stdout.strip().split('\n')

    # The tarball should NOT be staged (it's in .gitignore)
    assert 'rpms/pkg/pkg-2.0.tar.gz' not in staged_files
    # The .gitignore and spec should be staged
    assert 'rpms/pkg/.gitignore' in staged_files
    assert 'rpms/pkg/pkg.spec' in staged_files


#
# Tests — CLI (main function)
#


def test_cli_check_json_output(cuv_module, workdir: Path) -> None:
    """--json flag produces valid JSON output."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--json', 'pkg']), \
         pytest.raises(SystemExit) as exc_info, \
         patch('sys.stdout') as mock_stdout:
        # Capture print output
        printed = []
        mock_stdout.write = lambda s: printed.append(s)
        # The script calls sys.exit(1) when updates are found
        cuv_module.main()

    assert exc_info.value.code == 1  # updates found


def test_cli_no_updates_exit_zero(cuv_module, workdir: Path) -> None:
    """Exits with 0 when no updates are available."""
    _create_package(workdir, 'pkg', '2.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet', 'pkg']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 0


def test_cli_error_exit_two(cuv_module, workdir: Path) -> None:
    """Exits with 2 when errors occur and no updates are found."""
    _create_package(workdir, 'pkg', '1.0')
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    with patch.object(cuv_module, 'query_anitya',
                      side_effect=ConnectionError('timeout')), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet', 'pkg']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 2


def test_update_continues_after_package_failure(cuv_module, workdir: Path) -> None:
    """A failed package update is restored and does not block later packages."""
    _create_package(workdir, 'bad', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'good', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    subprocess.run(['git', 'add', '.'], cwd=workdir, check=True)
    subprocess.run(['git', 'commit', '-m', 'Add packages'], cwd=workdir, check=True)

    cuv_module.ROOT_DIR = workdir
    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    results = {
        'bad': cuv_module.VersionCheckResult(
            package='bad',
            current_version='1.0',
            upstream_version='2.0',
            has_update=True,
        ),
        'good': cuv_module.VersionCheckResult(
            package='good',
            current_version='1.0',
            upstream_version='2.0',
            has_update=True,
        ),
    }

    def fake_update(package: str, new_version: str) -> list[str]:
        spec_path = workdir / 'rpms' / package / f'{package}.spec'
        spec_path.write_text(
            spec_path.read_text().replace('Version: 1.0', f'Version: {new_version}')
        )
        metadata_path = workdir / 'metadata' / f'{package}.json'
        metadata = json.loads(metadata_path.read_text())
        metadata['version'] = new_version
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')

        if package == 'bad':
            raise cuv_module.urllib.error.HTTPError(
                'https://example.com/bad-2.0.tar.gz', 404, 'Not Found', {}, None
            )
        return []

    with patch.object(cuv_module, 'check_package_version',
                      side_effect=lambda package, distro='Fedora': results[package]), \
         patch.object(cuv_module, 'update_spec_version', side_effect=fake_update), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--update',
                            '--quiet', 'bad', 'good']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 1
    assert 'Version: 1.0' in (workdir / 'rpms' / 'bad' / 'bad.spec').read_text()
    assert 'Version: 2.0' in (workdir / 'rpms' / 'good' / 'good.spec').read_text()

    bad_metadata = json.loads((workdir / 'metadata' / 'bad.json').read_text())
    good_metadata = json.loads((workdir / 'metadata' / 'good.json').read_text())
    assert bad_metadata['version'] == '1.0'
    assert good_metadata['version'] == '2.0'

    subject = subprocess.run(
        ['git', 'log', '-1', '--format=%s'],
        cwd=workdir, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert subject == 'Update good to 2.0'


#
# Tests — track_upstream filtering
#


def test_main_skips_packages_without_track_upstream(cuv_module, workdir: Path) -> None:
    """main() skips packages without track_upstream when no CLI args given."""
    _create_package(workdir, 'tracked', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'untracked', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.0',
        'stable_versions': ['1.0'],
        'id': 42,
    }

    checked = []
    original_check = cuv_module.check_package_version

    def tracking_check(pkg, distro='Fedora'):
        checked.append(pkg)
        return original_check(pkg, distro)

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch.object(cuv_module, 'check_package_version', side_effect=tracking_check), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet']), \
         pytest.raises(SystemExit):
        cuv_module.main()

    assert 'tracked' in checked
    assert 'untracked' not in checked


def test_main_checks_tracked_packages(cuv_module, workdir: Path) -> None:
    """main() checks packages that have track_upstream: true."""
    _create_package(workdir, 'pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    # Exit code 1 means updates found
    assert exc_info.value.code == 1


def test_main_explicit_args_bypass_track_filter(cuv_module, workdir: Path) -> None:
    """Explicit CLI package args bypass the track_upstream filter."""
    _create_package(workdir, 'untracked', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet', 'untracked']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    # Should check the package even though it doesn't have track_upstream
    assert exc_info.value.code == 1


#
# Tests — list subcommand
#


def test_list_subcommand_includes_all_packages(
    cuv_module, workdir: Path, capsys
) -> None:
    """list subcommand includes both tracked and untracked packages."""
    _create_package(workdir, 'tracked-pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'untracked-pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.0',
        'stable_versions': ['1.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'list', '--quiet']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert 'tracked-pkg' in output
    assert 'untracked-pkg' in output


def test_list_subcommand_shows_not_found(
    cuv_module, workdir: Path, capsys
) -> None:
    """list subcommand shows not-found status for missing packages."""
    _create_package(workdir, 'missing-pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    with patch.object(cuv_module, 'query_anitya',
                      side_effect=ValueError('Package not found in Fedora')), \
         patch('sys.argv', ['check_upstream_versions.py', 'list', '--quiet']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert 'missing-pkg' in output
    assert 'not found in release-monitoring.org' in output


def test_list_subcommand_json_output(
    cuv_module, workdir: Path, capsys
) -> None:
    """list subcommand with --json produces valid JSON with all packages."""
    _create_package(workdir, 'alpha', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'beta', '2.0',
                    metadata={'version': '2.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'list',
                            '--quiet', '--json']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert isinstance(data, list)
    names = [entry['package'] for entry in data]
    assert 'alpha' in names
    assert 'beta' in names
    # Verify sorted alphabetically
    assert names == sorted(names)


def test_list_subcommand_shows_tracking_status(
    cuv_module, workdir: Path, capsys
) -> None:
    """list subcommand shows correct tracking status from metadata."""
    _create_package(workdir, 'tracked', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'untracked', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.0',
        'stable_versions': ['1.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'list',
                            '--quiet', '--json']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    data = json.loads(output)

    by_name = {entry['package']: entry for entry in data}
    assert by_name['tracked']['tracking'] == 'yes'
    assert by_name['untracked']['tracking'] == 'no'


#
# Tests — check subcommand
#


def test_check_subcommand_filters_tracked(cuv_module, workdir: Path) -> None:
    """check subcommand only checks packages with track_upstream: true."""
    _create_package(workdir, 'tracked', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'track_upstream': 'latest'})
    _create_package(workdir, 'untracked', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '1.0',
        'stable_versions': ['1.0'],
        'id': 42,
    }

    checked = []
    original_check = cuv_module.check_package_version

    def tracking_check(pkg, distro='Fedora'):
        checked.append(pkg)
        return original_check(pkg, distro)

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch.object(cuv_module, 'check_package_version',
                      side_effect=tracking_check), \
         patch('sys.argv', ['check_upstream_versions.py', 'check', '--quiet']), \
         pytest.raises(SystemExit):
        cuv_module.main()

    assert 'tracked' in checked
    assert 'untracked' not in checked


def test_check_subcommand_with_packages(cuv_module, workdir: Path) -> None:
    """check subcommand with explicit packages bypasses track filter."""
    _create_package(workdir, 'untracked', '1.0',
                    metadata={'version': '1.0', 'release': '1'})

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0',
        'stable_versions': ['2.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya', return_value=mock_response), \
         patch('sys.argv', ['check_upstream_versions.py', 'check',
                            '--quiet', 'untracked']), \
         pytest.raises(SystemExit) as exc_info:
        cuv_module.main()

    assert exc_info.value.code == 1


#
# _parse_gitlab_upstream_url
#


def test_parse_gitlab_upstream_url_simple(cuv_module) -> None:
    """Standard GitLab URL is split into API base and encoded project path."""
    api_base, project_path = cuv_module._parse_gitlab_upstream_url(
        "https://gitlab.com/redhat/hummingbird/src/nodejs-lts"
    )
    assert api_base == "https://gitlab.com/api/v4"
    assert project_path == "redhat%2Fhummingbird%2Fsrc%2Fnodejs-lts"


def test_parse_gitlab_upstream_url_trailing_slash(cuv_module) -> None:
    """Trailing slash on the URL is stripped before parsing."""
    api_base, project_path = cuv_module._parse_gitlab_upstream_url(
        "https://gitlab.example.org/group/project/"
    )
    assert api_base == "https://gitlab.example.org/api/v4"
    assert project_path == "group%2Fproject"


#
# _PRERELEASE_RE filtering
#


def test_prerelease_re_filters_known_labels(cuv_module) -> None:
    """Pre-release tags (alpha, beta, rc, nightly, dev, pre) are filtered."""
    for label in ("alpha", "beta", "rc1", "rc", "nightly", "dev", "pre"):
        assert cuv_module._PRERELEASE_RE.search(f"1.0.0-{label}"), \
            f"Expected {label!r} to match as pre-release"


def test_prerelease_re_filters_without_separator(cuv_module) -> None:
    """Pre-release labels directly after a digit (no hyphen) are still caught."""
    for tag in ("20.20.3rc1", "1.0.0alpha", "2.5beta2", "3.0.0pre"):
        assert cuv_module._PRERELEASE_RE.search(tag), \
            f"Expected {tag!r} to match as pre-release"


def test_prerelease_re_allows_similar_words(cuv_module) -> None:
    """Words containing pre-release substrings but not matching as whole words pass."""
    for label in ("prebuilt", "preview", "developer", "betamax", "rcfile"):
        assert not cuv_module._PRERELEASE_RE.search(f"1.0.0-{label}"), \
            f"Expected {label!r} to NOT match as pre-release"


#
# query_gitlab_tags
#


def _make_gitlab_response(tags_json, next_page=None):
    """Build a mock context-manager response for urlopen."""
    from io import BytesIO
    body = BytesIO(json.dumps(tags_json).encode("utf-8"))
    headers = Message()
    if next_page is not None:
        headers["x-next-page"] = str(next_page)
    response = type("FakeResponse", (), {
        "read": body.read,
        "headers": headers,
        "__enter__": lambda self: self,
        "__exit__": lambda self, *a: None,
    })()
    return response


def test_query_gitlab_tags_basic(cuv_module) -> None:
    """Returns sorted versions with prefix stripped."""
    tags = [
        {"name": "v2.0.0"},
        {"name": "v1.5.0"},
        {"name": "v1.0.0"},
    ]
    mock_resp = _make_gitlab_response(tags)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "test-token"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project", tag_strip_prefix="v"
        )

    assert result["version"] == "2.0.0"
    assert result["stable_versions"] == ["2.0.0", "1.5.0", "1.0.0"]


def test_query_gitlab_tags_filters_prereleases(cuv_module) -> None:
    """Pre-release tags are excluded from results."""
    tags = [
        {"name": "v3.0.0-rc1"},
        {"name": "v2.0.0"},
        {"name": "v2.0.0-beta"},
        {"name": "v1.0.0-alpha"},
        {"name": "v1.0.0"},
    ]
    mock_resp = _make_gitlab_response(tags)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "test-token"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert result["stable_versions"] == ["2.0.0", "1.0.0"]
    assert result["version"] == "2.0.0"


def test_query_gitlab_tags_skips_non_version_tags(cuv_module) -> None:
    """Tags that don't start with a digit after prefix stripping are skipped."""
    tags = [
        {"name": "latest"},
        {"name": "v2.0.0"},
        {"name": "release-candidate"},
    ]
    mock_resp = _make_gitlab_response(tags)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "test-token"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert result["stable_versions"] == ["2.0.0"]


def test_query_gitlab_tags_404_with_token_suggests_scope(cuv_module) -> None:
    """404 with a token set hints at incorrect URL or insufficient scope."""
    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://gitlab.com/api/v4/projects/x/repository/tags',
            404, 'Not Found', Message(), None,
        ),
    ), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        with pytest.raises(ValueError, match="read_api scope"):
            cuv_module.query_gitlab_tags("https://gitlab.com/group/project")


def test_query_gitlab_tags_404_without_token_suggests_auth(cuv_module) -> None:
    """404 without a token hints at missing authentication."""
    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://gitlab.com/api/v4/projects/x/repository/tags',
            404, 'Not Found', Message(), None,
        ),
    ), \
         patch.dict('os.environ', {}, clear=True), \
         patch.object(cuv_module.time, 'sleep'):
        with pytest.raises(ValueError, match="CHORE_MR_GITLAB_TOKEN"):
            cuv_module.query_gitlab_tags("https://gitlab.com/group/project")


def test_query_gitlab_tags_401_raises_valueerror(cuv_module) -> None:
    """401 from GitLab raises ValueError mentioning auth token."""
    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://gitlab.com/api/v4/projects/x/repository/tags',
            401, 'Unauthorized', Message(), None,
        ),
    ), \
         patch.dict('os.environ', {}), \
         patch.object(cuv_module.time, 'sleep'):
        with pytest.raises(ValueError, match="authentication failed.*CHORE_MR_GITLAB_TOKEN"):
            cuv_module.query_gitlab_tags("https://gitlab.com/group/project")


def test_query_gitlab_tags_403_raises_valueerror(cuv_module) -> None:
    """403 from GitLab raises ValueError mentioning auth token."""
    with patch.object(
        cuv_module.urllib.request, 'urlopen',
        side_effect=urllib.error.HTTPError(
            'https://gitlab.com/api/v4/projects/x/repository/tags',
            403, 'Forbidden', Message(), None,
        ),
    ), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        with pytest.raises(ValueError, match="authentication failed"):
            cuv_module.query_gitlab_tags("https://gitlab.com/group/project")


def test_query_gitlab_tags_pagination(cuv_module) -> None:
    """Multiple pages are fetched when x-next-page header is present."""
    page1 = _make_gitlab_response([{"name": "v2.0.0"}], next_page="2")
    page2 = _make_gitlab_response([{"name": "v1.0.0"}])

    with patch.object(
        cuv_module.urllib.request, 'urlopen', side_effect=[page1, page2]
    ), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert result["stable_versions"] == ["2.0.0", "1.0.0"]


def test_query_gitlab_tags_pagination_cap_warning(cuv_module, caplog) -> None:
    """Fetching 5 full pages with a next-page header triggers a cap warning."""
    pages = [
        _make_gitlab_response([{"name": f"v1.0.{i}"}], next_page=str(i + 2))
        for i in range(5)
    ]

    with patch.object(cuv_module.urllib.request, 'urlopen', side_effect=pages), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'), \
         caplog.at_level('WARNING'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert len(result["stable_versions"]) == 5
    assert "pagination reached" in caplog.text


def test_query_gitlab_tags_non_numeric_next_page(cuv_module, caplog) -> None:
    """Non-numeric x-next-page header stops pagination with a warning."""
    resp = _make_gitlab_response([{"name": "v1.0.0"}], next_page="invalid")

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'), \
         caplog.at_level('WARNING'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert result["stable_versions"] == ["1.0.0"]
    assert "Non-numeric x-next-page" in caplog.text


def test_query_gitlab_tags_empty_result(cuv_module) -> None:
    """No tags returns None version and empty stable_versions list."""
    mock_resp = _make_gitlab_response([])

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project"
        )

    assert result["version"] is None
    assert result["stable_versions"] == []


def test_query_gitlab_tags_search_scoped_by_track_version(cuv_module) -> None:
    """When track_version is set, the API URL includes a search parameter."""
    tags = [{"name": "v20.20.2"}, {"name": "v20.20.1"}]
    mock_resp = _make_gitlab_response(tags)

    calls = []
    original_Request = cuv_module.urllib.request.Request

    def capture_request(url, **kwargs):
        calls.append(url)
        return original_Request(url, **kwargs)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.object(cuv_module.urllib.request, 'Request', side_effect=capture_request), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project",
            tag_strip_prefix="v",
            track_version="20",
        )

    assert result["stable_versions"] == ["20.20.2", "20.20.1"]
    assert "search=v20" in calls[0]


def test_query_gitlab_tags_no_search_without_track_version(cuv_module) -> None:
    """Without track_version, no search parameter is added to the URL."""
    tags = [{"name": "v2.0.0"}]
    mock_resp = _make_gitlab_response(tags)

    calls = []
    original_Request = cuv_module.urllib.request.Request

    def capture_request(url, **kwargs):
        calls.append(url)
        return original_Request(url, **kwargs)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.object(cuv_module.urllib.request, 'Request', side_effect=capture_request), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project",
            tag_strip_prefix="v",
        )

    assert "search=" not in calls[0]


def test_query_gitlab_tags_no_prefix_strip(cuv_module) -> None:
    """Tags without a prefix are returned as-is when tag_strip_prefix is empty."""
    tags = [{"name": "2.0.0"}, {"name": "1.0.0"}]
    mock_resp = _make_gitlab_response(tags)

    with patch.object(cuv_module.urllib.request, 'urlopen', return_value=mock_resp), \
         patch.dict('os.environ', {"CHORE_MR_GITLAB_TOKEN": "tok"}), \
         patch.object(cuv_module.time, 'sleep'):
        result = cuv_module.query_gitlab_tags(
            "https://gitlab.com/group/project", tag_strip_prefix=""
        )

    assert result["stable_versions"] == ["2.0.0", "1.0.0"]


#
# version_source in VersionCheckResult
#


def test_check_package_version_gitlab_tags_sets_version_source(
    cuv_module, workdir: Path,
) -> None:
    """version_source field is populated in VersionCheckResult for gitlab_tags."""
    _create_package(workdir, 'mypackage', '1.0.0',
                    metadata={
                        'version': '1.0.0',
                        'version_source': 'gitlab_tags',
                        'upstream_repo': 'https://gitlab.com/group/project',
                        'tag_strip_prefix': 'v',
                    })

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0.0',
        'stable_versions': ['2.0.0', '1.0.0'],
    }

    with patch.object(cuv_module, 'query_gitlab_tags', return_value=mock_response):
        result = cuv_module.check_package_version('mypackage')

    assert result.version_source == "gitlab_tags"
    assert result.has_update is True
    assert result.upstream_version == '2.0.0'


def test_check_package_version_anitya_version_source_is_none(
    cuv_module, workdir: Path,
) -> None:
    """version_source field is None for standard Anitya-based lookups."""
    _create_package(workdir, 'mypackage', '1.0.0',
                    metadata={
                        'version': '1.0.0',
                        'release_monitoring_project_id': 42,
                    })

    cuv_module.RPMS_DIR = workdir / 'rpms'
    cuv_module.METADATA_DIR = workdir / 'metadata'

    mock_response = {
        'version': '2.0.0',
        'stable_versions': ['2.0.0', '1.0.0'],
        'id': 42,
    }

    with patch.object(cuv_module, 'query_anitya_by_project_id', return_value=mock_response):
        result = cuv_module.check_package_version('mypackage')

    assert result.version_source is None


#
# _get_package_lookaside_bucket
#


def test_get_package_lookaside_bucket_unknown_url_raises(cuv_module, tmp_path: Path) -> None:
    """Unknown lookaside_cache_url must raise, not silently fall back to the public bucket."""
    overrides = tmp_path / 'package-overrides.yaml'
    overrides.write_text("mypkg:\n  lookaside_cache_url: https://unknown.example.com/\n")
    cuv_module.PACKAGE_OVERRIDES_YAML = overrides

    with pytest.raises(ValueError, match='LOOKASIDE_BUCKET_BY_CACHE_URL'):
        cuv_module._get_package_lookaside_bucket('mypkg')


def test_get_package_lookaside_bucket_no_overrides_file_returns_default(
    cuv_module, tmp_path: Path,
) -> None:
    """Absent package-overrides.yaml falls back to DEFAULT_LOOKASIDE_BUCKET."""
    cuv_module.PACKAGE_OVERRIDES_YAML = tmp_path / 'nonexistent.yaml'

    bucket = cuv_module._get_package_lookaside_bucket('anypkg')

    assert bucket == cuv_module.DEFAULT_LOOKASIDE_BUCKET


def test_get_package_lookaside_bucket_known_url_returns_mapped_bucket(
    cuv_module, tmp_path: Path,
) -> None:
    """A lookaside_cache_url with a known mapping resolves to its private bucket."""
    known_url = next(iter(cuv_module.LOOKASIDE_BUCKET_BY_CACHE_URL))
    expected_bucket = cuv_module.LOOKASIDE_BUCKET_BY_CACHE_URL[known_url]

    overrides = tmp_path / 'package-overrides.yaml'
    overrides.write_text(f"mypkg:\n  lookaside_cache_url: {known_url}\n")
    cuv_module.PACKAGE_OVERRIDES_YAML = overrides

    assert cuv_module._get_package_lookaside_bucket('mypkg') == expected_bucket


#
# _git_clone_env_for_upstream_repo
#


def test_git_clone_env_missing_token_raises(cuv_module) -> None:
    """Private-prefix repo with the matching token env var unset raises ValueError."""
    with patch.dict('os.environ', {}, clear=True):
        with pytest.raises(ValueError, match='HUMMINGBIRD_SRC_GITLAB_TOKEN'):
            cuv_module._git_clone_env_for_upstream_repo(
                'https://gitlab.com/redhat/hummingbird/src/nodejs-lts'
            )


def test_git_clone_env_public_repo_unchanged(cuv_module) -> None:
    """A public upstream_repo (no private-prefix match) returns the env unmodified."""
    with patch.dict('os.environ', {'SOME_VAR': 'val'}, clear=True):
        env = cuv_module._git_clone_env_for_upstream_repo('https://github.com/nodejs/node')

    assert 'GIT_CONFIG_COUNT' not in env
    assert env['SOME_VAR'] == 'val'


def test_git_clone_env_none_upstream_repo_unchanged(cuv_module) -> None:
    """A None upstream_repo (no --upstream-repo passed) returns the env unmodified."""
    with patch.dict('os.environ', {'SOME_VAR': 'val'}, clear=True):
        env = cuv_module._git_clone_env_for_upstream_repo(None)

    assert 'GIT_CONFIG_COUNT' not in env
    assert env['SOME_VAR'] == 'val'


def test_git_clone_env_private_repo_injects_config_rewrite(cuv_module) -> None:
    """A private-prefix repo with the token set injects the url.insteadOf rewrite,
    scoped to this subprocess env only -- not the real global git config."""
    with patch.dict(
        'os.environ',
        {'HUMMINGBIRD_SRC_GITLAB_TOKEN': 's3cr3t'},
        clear=True,
    ):
        env = cuv_module._git_clone_env_for_upstream_repo(
            'https://gitlab.com/redhat/hummingbird/src/nodejs-lts'
        )

    assert env['GIT_CONFIG_COUNT'] == '1'
    assert env['GIT_CONFIG_KEY_0'] == (
        'url.https://oauth2:s3cr3t@gitlab.com/redhat/hummingbird/src/.insteadOf'
    )
    assert env['GIT_CONFIG_VALUE_0'] == 'https://gitlab.com/redhat/hummingbird/src/'
