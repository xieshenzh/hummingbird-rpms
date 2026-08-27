"""Validates the centralized upstream GPG keyrings in metadata/gpg-keys/.

Packages that verify their upstream source with gorget's
``verify: [{type: gpg-signature}]`` step point at a keyring stored centrally
under ``metadata/gpg-keys/<project>.gpg`` (passed to gorget as
``--gpg-keys-dir``; see ci/check_upstream_versions.py and
documentation/background/source-pipeline-tool.md). Centralizing the keys makes the
full set of trusted signers auditable in one place, but only if every file
there really is a usable public key and every pipeline that names a keyring
actually finds it. These tests enforce both.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
METADATA_DIR = ROOT_DIR / "metadata"
GPG_KEYS_DIR = METADATA_DIR / "gpg-keys"

PIPELINE_SUFFIX = ".source-pipeline.yaml"

# developer host may not have gpg
_GPG = shutil.which("gpg")


def _gpg_key_files() -> list[Path]:
    """Return sorted keyring files under metadata/gpg-keys/.

    Skips hidden files (e.g. a future ``.gitkeep``/``.gitignore`` used to keep
    the directory in git) so git housekeeping isn't parametrized as a key. Any
    other file is validated -- deliberately not restricted to ``.gpg`` so an
    armored key isn't silently left unchecked.
    """
    if not GPG_KEYS_DIR.is_dir():
        return []
    return sorted(
        p for p in GPG_KEYS_DIR.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )


def _gpg_verify_keyrings() -> list[tuple[str, str]]:
    """Return (pipeline_package, keyring_filename) for every gpg-signature step.

    Scans metadata/*.source-pipeline.yaml for
    ``verify: [{type: gpg-signature, keyring: <name>}]`` entries.
    """
    pairs: list[tuple[str, str]] = []
    if not METADATA_DIR.is_dir():
        return pairs
    for pipeline in sorted(METADATA_DIR.glob(f"*{PIPELINE_SUFFIX}")):
        package = pipeline.name[: -len(PIPELINE_SUFFIX)]
        data = yaml.safe_load(pipeline.read_text()) or {}
        for step in data.get("verify") or []:
            if isinstance(step, dict) and step.get("type") == "gpg-signature":
                keyring = step.get("keyring")
                if keyring:
                    pairs.append((package, keyring))
    return pairs


@pytest.mark.parametrize("keyfile", _gpg_key_files(), ids=lambda p: p.name)
def test_keyfile_is_valid_gpg_key(keyfile: Path) -> None:
    """Each file in metadata/gpg-keys/ must parse as a public GPG key.

    Uses ``gpg --show-keys`` in a throwaway homedir where it parses and lists
    the file's keys without importing anything or touching real GPG state, and
    exits non-zero on anything that isn't a valid keyring (empty file,
    truncated download, stray HTML, etc.).
    """
    assert _GPG is not None, (
        "gpg was not found on PATH, but is required to validate metadata/gpg-keys/. "
        "Run the suite via `make check` (the CI image ships gnupg2)."
    )
    with tempfile.TemporaryDirectory(prefix="gpg-keys-test-") as home:
        try:
            result = subprocess.run(
                [_GPG, "--homedir", home, "--batch", "--no-tty",
                 "--show-keys", "--with-colons", str(keyfile)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"metadata/gpg-keys/{keyfile.name}: gpg --show-keys timed out "
                f"after 30s (possibly a malformed key file)."
            )
    assert result.returncode == 0, (
        f"metadata/gpg-keys/{keyfile.name} is not a valid GPG key "
        f"(gpg exit {result.returncode}):\n{result.stderr}"
    )
    assert any(line.startswith("pub:") for line in result.stdout.splitlines()), (
        f"metadata/gpg-keys/{keyfile.name} parsed but contains no public key.\n"
        f"gpg output:\n{result.stdout}"
    )


@pytest.mark.parametrize(
    "package,keyring",
    _gpg_verify_keyrings(),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_referenced_keyring_exists(package: str, keyring: str) -> None:
    """Every keyring named by a gpg-signature verify step must exist.

    Catches a typo in a pipeline's ``keyring:`` field at CI time instead of
    when the source-fetch pipeline runs during a real update.
    """
    keyring_path = (GPG_KEYS_DIR / keyring).resolve()
    gpg_keys_root = GPG_KEYS_DIR.resolve()
    assert keyring_path.is_relative_to(gpg_keys_root), (
        f"metadata/{package}{PIPELINE_SUFFIX}: keyring value '{keyring}' "
        f"escapes metadata/gpg-keys/ (path traversal or absolute path)."
    )
    assert keyring_path.is_file(), (
        f"metadata/{package}{PIPELINE_SUFFIX} references keyring "
        f"'{keyring}', but metadata/gpg-keys/{keyring} does not exist.\n"
        f"Add the key file, or fix the 'keyring:' value in the pipeline."
    )
