"""Cross-checks that gorget pipeline transform steps stay in sync with spec Patches.

A `run:` transform step that hand-applies patches (e.g. to populate an offline
yarn/pnpm cache from a patched lockfile) duplicates the spec's `PatchN:` list
rather than reading it. Nothing else keeps those two lists in sync: a patch
added to the spec via the normal backport workflow (see
documentation/operating/rebuilding-packages.md) is never automatically
reflected in the pipeline YAML.

This silently broke grafana12.4 and grafana13.1: four separate CVE-backport
commits added patches that bumped yarn.lock, each correctly listed in the
spec, but none of them updated the corresponding
metadata/grafana*.source-pipeline.yaml transform step. The generated yarn
cache silently went stale and only surfaced as an opaque "cache entry
required but missing" build failure on the next version bump, when the
pipeline's transform step finally ran again from a pristine checkout.

See documentation/design/source-pipeline-tool.md for background on the
pipeline tool.
"""

import re
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

PIPELINE_SUFFIX = ".source-pipeline.yaml"
PATCH_DECL_RE = re.compile(r"^Patch\d*:\s+(\S+)", re.MULTILINE)
PATCH_APPLY_RE = re.compile(r'patch\s+-p1\s*<\s*"?\$\{PACKAGE_DIR\}/([^"\s]+\.patch)"?')
DIFF_TARGET_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)

# Commands that resolve dependencies against a lockfile/manifest -- if a transform
# runs one of these, every spec patch touching that ecosystem's lockfile/manifest
# must be applied first, or the generated artifact (offline cache, vendor bundle)
# reflects a subset of the patches %build itself applies.
INSTALL_COMMAND_MANIFESTS = {
    r"\byarn\b.*\binstall\b": {"yarn.lock", "package.json"},
    r"\bnpm\b.*\b(install|ci)\b": {"package.json", "package-lock.json", "npm-shrinkwrap.json"},
    r"\bpnpm\b.*\b(install|fetch)\b": {"pnpm-lock.yaml", "package.json", "pnpm-workspace.yaml"},
    r"\bgo\b.*\bmod\b.*\bvendor\b": {"go.mod", "go.sum"},
    r"\bcargo\b.*\bvendor\b": {"Cargo.lock", "Cargo.toml"},
    r"\bcomposer\b.*\binstall\b": {"composer.lock", "composer.json"},
}


def _pipeline_packages() -> list[str]:
    """Return sorted package names that have a metadata/<package>.source-pipeline.yaml."""
    if not METADATA_DIR.is_dir():
        pytest.skip(f"metadata/ directory not found at {METADATA_DIR}")
    return sorted(p.name[: -len(PIPELINE_SUFFIX)] for p in METADATA_DIR.glob(f"*{PIPELINE_SUFFIX}"))


def _spec_path(package: str) -> Path | None:
    """Return the package's spec file. Directory and spec base name can differ
    for versioned packages (e.g. rpms/erlang27/erlang.spec)."""
    matches = sorted((RPMS_DIR / package).glob("*.spec"))
    return matches[0] if matches else None


def _transform_run_scripts(pipeline: dict) -> list[str]:
    """Return the shell script body of every `run:` transform step."""
    scripts = []
    for step in pipeline.get("transform") or []:
        if not isinstance(step, dict) or step.get("type") != "run":
            continue
        command = step.get("command") or []
        if command:
            scripts.append(command[-1])
    return scripts


def _applied_patches(scripts: list[str]) -> list[str]:
    """Return patch filenames applied via `patch -p1 < ...`, across all run steps, in order."""
    applied: list[str] = []
    for script in scripts:
        applied.extend(PATCH_APPLY_RE.findall(script))
    return applied


def _patch_touched_basenames(patch_path: Path) -> set[str]:
    """Return the basenames of files a patch modifies, from its `+++ b/...` lines.

    Basenames rather than full paths: a monorepo lockfile-resolving install can be
    sensitive to a manifest file (e.g. package.json) anywhere in the tree, and a
    patch's path prefix need not match another patch's exactly for both to matter
    to the same install command.
    """
    if not patch_path.exists():
        return set()
    text = patch_path.read_text(errors="replace")
    return {PurePosixPath(p).name for p in DIFF_TARGET_RE.findall(text)}


def _relevant_manifests(scripts: list[str]) -> set[str]:
    """Return manifest/lockfile basenames that matter to any install-like command
    found in the transform scripts (see INSTALL_COMMAND_MANIFESTS)."""
    relevant: set[str] = set()
    for script in scripts:
        for pattern, manifests in INSTALL_COMMAND_MANIFESTS.items():
            if re.search(pattern, script):
                relevant |= manifests
    return relevant


@pytest.mark.parametrize("package", _pipeline_packages())
def test_transform_patches_cover_files_they_touch(package: str) -> None:
    """A pipeline transform that applies patches must also apply any other
    spec-declared patch touching a lockfile/manifest relevant to the transform's
    own install command (or touching the same file as a patch it does apply) --
    otherwise the generated artifact reflects only some of the patches %build
    itself applies, and silently goes stale."""
    pipeline_path = METADATA_DIR / f"{package}{PIPELINE_SUFFIX}"
    pipeline = yaml.safe_load(pipeline_path.read_text())

    scripts = _transform_run_scripts(pipeline)
    applied = _applied_patches(scripts)
    if not applied:
        pytest.skip(f"{package}: no run: transform step applies patches")

    spec_path = _spec_path(package)
    assert spec_path is not None, f"{package}: no spec file found under rpms/{package}/"
    declared = PATCH_DECL_RE.findall(spec_path.read_text())

    package_dir = RPMS_DIR / package
    applied_basenames: set[str] = set()
    for name in applied:
        applied_basenames |= _patch_touched_basenames(package_dir / name)

    relevant = applied_basenames | _relevant_manifests(scripts)

    missing = []
    for name in declared:
        if name in applied or not name.endswith(".patch"):
            continue
        overlap = _patch_touched_basenames(package_dir / name) & relevant
        if overlap:
            missing.append((name, sorted(overlap)))

    assert not missing, (
        f"metadata/{package}{PIPELINE_SUFFIX}'s transform step doesn't apply "
        f"{', '.join(name for name, _ in missing)}, but "
        f"{'it touches' if len(missing) == 1 else 'they touch'} a lockfile/manifest "
        f"relevant to the transform's own install command or to a patch it DOES apply: "
        f"{'; '.join(f'{name} touches {files}' for name, files in missing)}.\n\n"
        f"The pipeline transform and the spec's %prep/%goprep must apply an identical "
        f"patch sequence for any file the transform's generated artifact depends on "
        f"(e.g. yarn.lock, pnpm-lock.yaml, go.sum) -- otherwise the generated artifact "
        f"silently goes stale relative to what %build actually applies. Add the missing "
        f"patch(es) to the `run:` transform step in the same relative order as their "
        f"PatchN declaration. See documentation/design/source-pipeline-tool.md."
    )
