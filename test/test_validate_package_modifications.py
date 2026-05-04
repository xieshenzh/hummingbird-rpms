"""Tests for validate_package_modifications."""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest


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

    module = types.ModuleType('validate_package_modifications')
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)

    # Also patch the module's own references
    module.METADATA_DIR = tmp_path / 'metadata'  # type: ignore[attr-defined]
    module.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    module.ROOT_DIR = tmp_path  # type: ignore[attr-defined]

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
