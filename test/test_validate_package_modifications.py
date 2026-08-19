"""Tests for validate_package_modifications."""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml


#
# Fixtures
#


@pytest.fixture
def validator(tmp_path: Path):
    """Load validate_package_modifications.py with paths pointed at tmp_path."""
    # Set up a git repo so the module's dist_git import can call git
    subprocess.run(['git', 'init'], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=tmp_path, check=True)

    (tmp_path / 'rpms').mkdir()
    (tmp_path / 'metadata').mkdir()
    (tmp_path / 'ci').mkdir()

    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit', '--allow-empty'],
                   cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Load the module with ROOT_DIR/RPMS_DIR/METADATA_DIR patched via dist_git
    script_path = Path(__file__).parent.parent / 'ci' / 'validate_package_modifications.py'

    # First, load dist_git so we can patch its paths before validate imports them
    dist_git_path = Path(__file__).parent.parent / 'ci' / 'dist_git.py'
    dg = types.ModuleType('dist_git')
    dg.__file__ = str(dist_git_path)
    code = compile(dist_git_path.read_text(), str(dist_git_path), 'exec')
    exec(code, dg.__dict__)
    sys.modules['dist_git'] = dg

    # Patch dist_git paths so validate_package_modifications picks them up
    dg.ROOT_DIR = tmp_path  # type: ignore[attr-defined]
    dg.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    dg.METADATA_DIR = tmp_path / 'metadata'  # type: ignore[attr-defined]
    dg.PACKAGE_OVERRIDES_YAML = tmp_path / 'ci' / 'package-overrides.yaml'  # type: ignore[attr-defined]

    module = types.ModuleType('validate_package_modifications')
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)

    # Also patch the module's own references
    module.METADATA_DIR = tmp_path / 'metadata'  # type: ignore[attr-defined]
    module.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    module.ROOT_DIR = tmp_path  # type: ignore[attr-defined]
    module.PACKAGE_OVERRIDES_YAML = tmp_path / 'ci' / 'package-overrides.yaml'  # type: ignore[attr-defined]

    module._tmp_path = tmp_path  # type: ignore[attr-defined]
    return module


def _create_package(root: Path, name: str, metadata: dict) -> None:
    """Create a package directory with a spec file and metadata."""
    pkg_dir = root / 'rpms' / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    spec = pkg_dir / f'{name}.spec'
    spec.write_text(f"""\
Name: {name}
Version: 1.0
Release: 1%{{?dist}}
Summary: Test
License: MIT

%description
Test
""")

    meta_file = root / 'metadata' / f'{name}.json'
    with open(meta_file, 'w') as f:
        json.dump(metadata, f, indent=2)
        f.write('\n')


#
# Tests — track_upstream type validation
#


def test_track_upstream_boolean_rejected(validator) -> None:
    """track_upstream=true (boolean) fails validation; must be a string."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'source': 'https://example.com',
        'branch': 'main',
        'sha': 'abc123',
        'track_upstream': True,
    })
    # Commit the package so git history checks have something
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'track_upstream' in error
    assert 'bool' in error


def test_track_upstream_string_latest_accepted(validator) -> None:
    """track_upstream="latest" (string) passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'source': 'https://example.com',
        'branch': 'main',
        'sha': 'abc123',
        'track_upstream': 'latest',
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_track_upstream_version_prefix_accepted(validator) -> None:
    """track_upstream="1.26" (version prefix string) passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'source': 'https://example.com',
        'branch': 'main',
        'sha': 'abc123',
        'track_upstream': '1.26',
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_track_upstream_integer_rejected(validator) -> None:
    """track_upstream=42 (integer) fails validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {
        'modification_status': 'clean',
        'source': 'https://example.com',
        'branch': 'main',
        'sha': 'abc123',
        'track_upstream': 42,
    })
    subprocess.run(['git', 'add', '.'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'Sync pkg from 1.0-1 to 1.0-1',
                     '--trailer', 'Upstream: https://example.com'],
                   cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'track_upstream' in error
    assert 'int' in error


#
# Tests — independent packages require forked_from/lookaside_cache_url
#


def _write_overrides(root: Path, overrides: dict) -> None:
    (root / 'ci' / 'package-overrides.yaml').write_text(yaml.safe_dump(overrides))


def _write_sources(root: Path, name: str, content: str) -> None:
    (root / 'rpms' / name / 'sources').write_text(content)


def test_independent_package_missing_lookaside_override_rejected(validator) -> None:
    """An independent package with remote sources but no forked_from/
    lookaside_cache_url override fails.

    Without one of these, the build's source-fetch step defaults to Fedora's
    lookaside cache, where an independent package's sources were never uploaded.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {'modification_status': 'independent'})
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {})

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'forked_from' in error


def test_independent_package_with_no_overrides_file_and_sources_rejected(validator) -> None:
    """An independent package with sources but no overrides file at all still fails.

    Exercises the PACKAGE_OVERRIDES_YAML.exists() == False branch, which
    falls back to an empty overrides dict rather than erroring out.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {'modification_status': 'independent'})
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    # Deliberately do NOT write ci/package-overrides.yaml

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert not valid
    assert 'forked_from' in error


def test_independent_package_with_forked_from_accepted(validator) -> None:
    """An independent package with forked_from set passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {'modification_status': 'independent'})
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {
        'pkg': {'forked_from': 'https://gitlab.com/redhat/hummingbird/rpms'},
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_independent_package_with_lookaside_cache_url_accepted(validator) -> None:
    """An independent package with lookaside_cache_url set passes validation."""
    root = validator._tmp_path
    _create_package(root, 'pkg', {'modification_status': 'independent'})
    _write_sources(root, 'pkg', 'SHA512 (pkg-1.0.tar.gz) = abc123\n')
    _write_overrides(root, {
        'pkg': {'lookaside_cache_url': 'https://example.cloudfront.net/'},
    })

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


def test_independent_package_with_no_sources_file_accepted(validator) -> None:
    """An independent package with no `sources` file (all Source files shipped
    locally, e.g. hummingbird-release) never hits the lookaside fetch path,
    so it doesn't need forked_from/lookaside_cache_url.
    """
    root = validator._tmp_path
    _create_package(root, 'pkg', {'modification_status': 'independent'})
    _write_overrides(root, {})

    valid, error = validator.validate_package('pkg', check_actual_state=False)
    assert valid, f"Expected valid but got: {error}"


#
# Tests — get_changed_packages_in_mr()
#


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_get_changed_packages_in_mr_uses_diff_base_sha_across_disconnected_history(
    validator, tmp_path_factory, monkeypatch,
):
    """CI_MERGE_REQUEST_DIFF_BASE_SHA is GitLab's own merge-base for this
    MR's diff. Using it directly (two-commit diff, no ancestry search) must
    work even when the base commit and HEAD share no local common ancestor
    -- exactly the shallow-clone shape that makes a origin/<target>...HEAD
    triple-dot diff fail with "no merge base" in a long-lived MR (see
    ci/check_nevr_conflicts.py's get_changed_rpm_packages(), which hit this
    in practice in !3887)."""
    root = validator.ROOT_DIR  # already an initialized git repo, per the `validator` fixture

    # Kept fully outside root's working tree (not a subdirectory of it) so
    # it can never be mistaken for a nested repo/submodule by a future
    # `git add .` in either repo.
    remote_repo = tmp_path_factory.mktemp('validate-remote')
    _git(['init', '--initial-branch=main'], remote_repo)
    _git(['config', 'user.name', 'Test'], remote_repo)
    _git(['config', 'user.email', 'test@example.com'], remote_repo)
    (remote_repo / 'rpms' / 'unchanged-pkg').mkdir(parents=True)
    (remote_repo / 'rpms' / 'unchanged-pkg' / 'unchanged-pkg.spec').write_text('v1\n')
    _git(['add', '.'], remote_repo)
    _git(['commit', '-m', 'Base commit'], remote_repo)
    base_sha = _git(['rev-parse', 'HEAD'], remote_repo).stdout.strip()

    _git(['remote', 'add', 'origin', str(remote_repo)], root)
    # Same path + content as the remote's base -- must NOT show as changed.
    (root / 'rpms' / 'unchanged-pkg').mkdir(parents=True)
    (root / 'rpms' / 'unchanged-pkg' / 'unchanged-pkg.spec').write_text('v1\n')
    # A genuinely new package -- must show as changed.
    (root / 'rpms' / 'changed-pkg').mkdir(parents=True)
    (root / 'rpms' / 'changed-pkg' / 'changed-pkg.spec').write_text('v1\n')
    _git(['add', 'rpms'], root)
    _git(['commit', '-m', 'MR commit'], root)

    monkeypatch.setenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', base_sha)
    monkeypatch.delenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', raising=False)

    result = validator.get_changed_packages_in_mr()

    assert result == ['changed-pkg']


def test_get_changed_packages_in_mr_diff_base_sha_fetch_failure_raises(validator, monkeypatch):
    """A bogus/unfetchable CI_MERGE_REQUEST_DIFF_BASE_SHA must fail (no
    'origin' remote is configured in the `validator` fixture's repo).

    Unlike check_nevr_conflicts.py (which has its own NevrCheckError to
    wrap failures in), this module has no custom exception type, so the
    function logs a clarifying message and then re-raises the original
    subprocess.CalledProcessError unchanged rather than inventing one just
    for this call site."""
    monkeypatch.setenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', '0' * 40)
    with pytest.raises(subprocess.CalledProcessError):
        validator.get_changed_packages_in_mr()


def test_get_changed_packages_in_mr_fallback_to_target_branch(validator, monkeypatch):
    """When CI_MERGE_REQUEST_DIFF_BASE_SHA is not set (e.g. running locally
    outside a GitLab MR pipeline), falls back to the pre-existing
    origin/<target>...HEAD triple-dot diff behavior."""
    root = validator.ROOT_DIR
    base_sha = _git(['rev-parse', 'HEAD'], root).stdout.strip()
    _git(['update-ref', 'refs/remotes/origin/main', base_sha], root)

    (root / 'rpms' / 'changed-pkg').mkdir(parents=True)
    (root / 'rpms' / 'changed-pkg' / 'changed-pkg.spec').write_text('v1\n')
    (root / 'metadata' / 'meta-only-pkg.json').write_text('{}\n')
    _git(['add', '.'], root)
    _git(['commit', '-m', 'MR commit'], root)

    monkeypatch.delenv('CI_MERGE_REQUEST_DIFF_BASE_SHA', raising=False)
    monkeypatch.delenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', raising=False)

    result = validator.get_changed_packages_in_mr()

    assert result == ['changed-pkg', 'meta-only-pkg']
