"""Tests for check_upstream_versions."""

import json
import subprocess
import types
from pathlib import Path
from unittest.mock import patch

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


def test_mark_package_modified_preserves_native(cuv_module, workdir: Path) -> None:
    """Does not overwrite native status with modified."""
    _create_package(workdir, 'pkg', '1.0',
                    metadata={'version': '1.0', 'release': '1',
                              'modification_status': 'native'})
    cuv_module.METADATA_DIR = workdir / 'metadata'
    cuv_module.mark_package_modified('pkg', 'Update to upstream version 2.0',
                                     version='2.0', release='1')

    with open(workdir / 'metadata' / 'pkg.json') as f:
        data = json.load(f)
    assert data['modification_status'] == 'native'
    assert 'modification_reason' not in data
    assert data['version'] == '2.0'
    assert data['release'] == '1'


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


def test_download_new_sources_no_version_in_filename(cuv_module, workdir: Path) -> None:
    """Skips sources where filename does not contain the version."""
    _create_package(
        workdir, 'pkg', '2.0',
        sources={'static-data.tar.gz': 'somehash'},
    )

    with patch.object(cuv_module, '_get_spec_source_urls',
                      return_value={0: 'https://example.com/static-data.tar.gz'}):

        cuv_module.RPMS_DIR = workdir / 'rpms'
        downloaded = cuv_module.download_new_sources('pkg', '1.0', '2.0')

    assert downloaded == []


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
# Tests — update_spec_version
#


def test_update_spec_version(cuv_module, workdir: Path) -> None:
    """Updates spec version, downloads sources, marks modified."""
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

    # Metadata should be marked as modified with updated version/release
    with open(workdir / 'metadata' / 'pkg.json') as f:
        data = json.load(f)
    assert data['modification_status'] == 'modified'
    assert 'Update to upstream version 2.0' in data['modification_reason']
    assert data['version'] == '2.0'
    assert data['release'] == '0.1'


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
    cuv_module, workdir: Path,
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

    with patch.object(cuv_module, 'download_new_sources', return_value=[]):
        cuv_module.update_spec_version('pkg', '2.0')

    # The hook should have created a marker file
    marker = pkg_dir / 'post-hook-marker'
    assert marker.exists()
    assert 'post-hook ran' in marker.read_text()


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
