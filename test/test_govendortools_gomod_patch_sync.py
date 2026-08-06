"""Cross-checks that go-vendor-tools.toml pre_commands mutating go.mod/go.sum
have a matching spec patch.

`[archive] pre_commands` in go-vendor-tools.toml (sed edits, `go get` bumps,
`go mod tidy`) only ever run against gorget's own vendor-archive checkout --
the plain source tarball (Source0) is fetched separately and never sees them.
If a pre_command mutates go.mod/go.sum without an equivalent spec patch
replicating that change onto the actual %prep/%build tree, go.mod there and
vendor/modules.txt in the generated vendor archive can end up requiring
different versions of the same package, which `go build -mod=vendor` rejects
as inconsistent vendoring.

This silently broke trivy (see documentation/design/source-pipeline-tool.md's
"Known sharp edge" section for the full incident writeup).

This is a different invariant from test_source_pipeline_patches.py (which
checks that a gorget pipeline's `transform:` step applies the same patches
the spec declares) -- this one applies to every package with a
go-vendor-tools.toml, whether or not it's migrated to gorget, and it's about
go-vendor-tools.toml's own pre_commands staying in sync with the spec's
patches, not about a pipeline transform step.

See documentation/operating/rebuilding-packages.md's "For Go packages that
vendor deps" guidance and .cursor/skills/cve/SKILL.md's "Needs backport"
step for the human-facing version of this rule.
"""

import re
from pathlib import Path

import pytest
import tomllib

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"

PATCH_DECL_RE = re.compile(r"^Patch\d*:\s+(\S+)", re.MULTILINE)
DIFF_TARGET_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
GOMOD_FILES = {"go.mod", "go.sum"}

# Matches a pre_command that mentions go.mod/go.sum by name (e.g. a `sed`/`rm`
# targeting the file), or runs a go subcommand known to rewrite them (`go get`,
# `go mod tidy`/`edit`). Deliberately doesn't match `go mod vendor`/`go build`
# etc., which read go.mod but don't rewrite its requirements. Intentionally
# over-approximates on the file-name arm -- a read-only reference like `cat
# go.mod` also matches -- since real-world pre_commands are argv arrays that
# mutate (sed/rm/go get/go mod tidy), not grep/cat calls; a false positive here
# just means double-checking a patch that turns out to already be unnecessary.
GOMOD_MUTATION_RE = re.compile(r"\bgo\.(?:mod|sum)\b|\bgo\s+get\b|\bgo\s+mod\s+(?:tidy|edit)\b")


def _packages_with_go_vendor_tools() -> list[str]:
    """Return sorted package names that have an rpms/<package>/go-vendor-tools.toml."""
    if not RPMS_DIR.is_dir():
        pytest.skip(f"rpms/ directory not found at {RPMS_DIR}")
    return sorted(p.parent.name for p in RPMS_DIR.glob("*/go-vendor-tools.toml"))


def _spec_path(package: str) -> Path | None:
    """Return the package's spec file. Directory and spec base name can differ
    for versioned packages (e.g. rpms/erlang27/erlang.spec)."""
    matches = sorted((RPMS_DIR / package).glob("*.spec"))
    return matches[0] if matches else None


def _pre_commands(toml_path: Path) -> list:
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    return data.get("archive", {}).get("pre_commands", [])


def _command_text(command: object) -> str:
    """Flatten a pre_command (a TOML array of argv-style strings, per
    go_vendor_archive's own convention) into one searchable string."""
    if isinstance(command, list):
        return " ".join(_command_text(part) for part in command)
    return str(command)


def _pre_commands_mutate_gomod(pre_commands: list) -> bool:
    return any(GOMOD_MUTATION_RE.search(_command_text(cmd)) for cmd in pre_commands)


def _patch_touches_gomod(patch_path: Path) -> bool:
    if not patch_path.exists():
        return False
    text = patch_path.read_text(errors="replace")
    targets = {Path(p).name for p in DIFF_TARGET_RE.findall(text)}
    return bool(targets & GOMOD_FILES)


@pytest.mark.parametrize("package", _packages_with_go_vendor_tools())
def test_gomod_mutating_precommands_have_a_matching_patch(package: str) -> None:
    """If go-vendor-tools.toml's pre_commands mutate go.mod/go.sum, at least one
    spec-declared patch must also touch go.mod or go.sum -- otherwise the plain
    source tarball's go.mod silently diverges from what the vendor archive was
    generated against."""
    toml_path = RPMS_DIR / package / "go-vendor-tools.toml"
    pre_commands = _pre_commands(toml_path)
    if not _pre_commands_mutate_gomod(pre_commands):
        pytest.skip(f"{package}: no pre_commands mutate go.mod/go.sum")

    spec_path = _spec_path(package)
    if spec_path is None:
        pytest.skip(f"{package}: no spec file found under rpms/{package}/")
    declared = PATCH_DECL_RE.findall(spec_path.read_text())

    package_dir = RPMS_DIR / package
    has_matching_patch = any(
        # Only patches declared with a .patch extension are checked; a patch named
        # .diff or with no extension is silently skipped here (same accepted
        # limitation as test_source_pipeline_patches.py).
        name.endswith(".patch") and _patch_touches_gomod(package_dir / name)
        for name in declared
    )

    assert has_matching_patch, (
        f"rpms/{package}/go-vendor-tools.toml's [archive] pre_commands mutate go.mod/go.sum "
        f"(a direct edit, `go get`, or `go mod tidy`/`edit`), but none of {package}'s spec "
        f"Patches touch go.mod or go.sum.\n\n"
        f"pre_commands only run against gorget's own vendor-archive checkout -- the plain "
        f"source tarball (Source0) never sees them, so go.mod in the actual %prep/%build tree "
        f"can require different versions than vendor/modules.txt in the generated vendor "
        f"archive, which `go build -mod=vendor` rejects as inconsistent vendoring. This is not "
        f"hypothetical: it silently broke trivy this way for over a week before an unrelated "
        f"version bump's %check run caught it.\n\n"
        f"Add a PatchN to {package}'s spec that applies the same go.mod/go.sum change the "
        f"pre_commands make, computed offline (pre_commands can't run in %prep -- Konflux "
        f"builds are hermetic, no network for `go get`). See "
        f"documentation/operating/rebuilding-packages.md's 'For Go packages that vendor deps' "
        f"guidance."
    )
