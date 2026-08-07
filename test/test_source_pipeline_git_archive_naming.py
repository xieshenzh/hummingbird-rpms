"""Cross-checks that a `fetch: type: git` step's fixed, non-version-tracked
`ref:` stays in sync with the spec.

A `type: git` fetch step whose `ref:` contains a `${VERSION}` token (e.g.
`v${VERSION}`) moves automatically with the package's version -- normal tag
tracking, no drift risk. A `ref:` with no `${VERSION}` token is a *fixed
pin* independent of version bumps: a development snapshot with no tagged
release (glibc, HUM-5619; php-patchwork-jsqueeze) or a fork point pinned for
some other reason.

A fixed ref alone doesn't force anything into the spec, though: gorget's
git fetch names the archive's internal directory after `archive_name`,
which defaults to `${PACKAGE}-${VERSION}` -- identical to what
`%setup`/`%autosetup` already assumes with no `-n` override. A package can
legitimately pin a fixed commit while using that default naming, with no
trace of the commit anywhere in the spec; that's not a drift risk, since
there's no separate literal identifier that could go stale. The risk only
exists once the spec is *also* forced into an explicit `-n <value>` that
isn't just the plain default -- meaning the archive's directory name
carries some other identifier (typically the commit itself) that only the
human refreshing the pin knows to keep in sync (glibc's
`%autosetup -n %{glibcsrcdir}` with `%global glibcsrcdir
glibc-2.43-47-gbc95068f5f`; php-patchwork-jsqueeze's
`%setup -qn %{name}-%{version}-%{github_commit}`). So this check requires
both: a fixed ref, and a non-default `-n` override.

Nothing today keeps a fixed `ref:` and that non-default `-n` value in sync:
`dist_git.py update` can cleanly 3-way-merge a Fedora spec change that
advances the pinned commit's spec-side representation without ever
touching `metadata/<package>.source-pipeline.yaml`, which lives outside the
`rpms/<package>/` directory the merge operates on. The mismatch then isn't
caught until a build fails on a missing extraction directory, or -- once
HUM-4621 lands and `update` starts auto-invoking gorget -- potentially
produces a self-consistent-but-wrong `sources` file with no human in the
loop, auto-committed by `ci/dist_git_update_multi_mr.sh`.

This check only compares the pinned ref's own short-SHA prefix (git's own
7-character abbreviation convention) against the spec text -- not the full
expected archive/directory name -- so it doesn't need to reconstruct
`%{name}`/`%{version}`-style macro composition (which can legitimately
differ per package, e.g. php-patchwork-jsqueeze's
`%{name}-%{github_version}-%{github_commit}`). It only asks: does the spec
reference the exact commit the pipeline is pinned to, at all.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
RPMS_DIR = ROOT_DIR / "rpms"
METADATA_DIR = ROOT_DIR / "metadata"

PIPELINE_SUFFIX = ".source-pipeline.yaml"

# Git's own default abbreviated-SHA length.
_SHORT_SHA_LEN = 7

_CHANGELOG_RE = re.compile(r"^%changelog\b", re.MULTILINE)

# Captures the argument of a %setup/%autosetup -n override, tolerating
# combined short flags (e.g. `-qn`) and other flags before/after it.
_SETUP_N_RE = re.compile(r"%(?:auto)?setup\b.*?-\w*n\s*(\S+)")

# Spellings of the plain default directory name that %setup/%autosetup
# already assumes with no -n override at all -- an explicit -n set to one
# of these is not a "custom" override and carries no identifier that could
# drift out of sync with a pinned ref.
_DEFAULT_N_VALUES = {"%{name}-%{version}", "%{name}%{version}"}


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


def _fixed_git_refs(pipeline: dict) -> list[str]:
    """Return `ref:` values of `fetch: type: git` steps that are NOT
    version-templated -- i.e. fixed pins independent of version bumps."""
    return [
        step["ref"]
        for step in pipeline.get("fetch") or []
        if isinstance(step, dict)
        and step.get("type") == "git"
        and step.get("ref")
        and "${VERSION}" not in step["ref"]
    ]


def _has_custom_setup_override(spec_text: str) -> bool:
    """True if any %setup/%autosetup has an explicit -n argument that isn't
    just the plain default directory name (i.e. it embeds some other
    identifier that must track the pinned commit). Checks every match, not
    just the first, since a multi-source %prep can have more than one
    %setup/%autosetup call."""
    return any(
        match.group(1) not in _DEFAULT_N_VALUES
        for match in _SETUP_N_RE.finditer(spec_text)
    )


@pytest.mark.parametrize("package", _pipeline_packages())
def test_git_fetch_fixed_ref_matches_spec(package: str) -> None:
    """A `fetch: type: git` step pinned to a fixed (non-version-templated)
    ref, whose spec also carries a custom (non-default) %setup/%autosetup
    -n override, must have that commit's short SHA referenced somewhere in
    the spec -- otherwise the pipeline's pin and the spec's own reference to
    the snapshotted commit have drifted apart."""
    pipeline_path = METADATA_DIR / f"{package}{PIPELINE_SUFFIX}"
    pipeline = yaml.safe_load(pipeline_path.read_text())

    fixed_refs = _fixed_git_refs(pipeline)
    if not fixed_refs:
        pytest.skip(f"{package}: no fixed (non-version-templated) git fetch ref")

    spec_path = _spec_path(package)
    if spec_path is None:
        pytest.skip(f"{package}: no spec file found under rpms/{package}/")
    spec_text = spec_path.read_text()
    # Exclude %changelog: an old commit's short SHA can persist there forever
    # as history, which would mask a stale ref that's no longer referenced
    # anywhere in the package's *current* (pre-changelog) spec content.
    changelog_match = _CHANGELOG_RE.search(spec_text)
    if changelog_match:
        spec_text = spec_text[: changelog_match.start()]

    if not _has_custom_setup_override(spec_text):
        pytest.skip(
            f"{package}: no custom %setup/%autosetup -n override -- "
            f"default naming carries no identifier that could drift"
        )

    missing = [ref for ref in fixed_refs if ref[:_SHORT_SHA_LEN] not in spec_text]

    assert not missing, (
        f"metadata/{package}{PIPELINE_SUFFIX}'s fetch step is pinned to "
        f"{', '.join(missing)}, but the first {_SHORT_SHA_LEN}-character "
        f"prefix of {'that ref does' if len(missing) == 1 else 'those refs do'} "
        f"not appear anywhere in rpms/{package}/*.spec (outside %changelog), "
        f"even though the spec has a custom %setup/%autosetup -n override. "
        f"That override and the pipeline's `ref:` have drifted apart -- "
        f"update the spec and the pipeline's `ref:` together. See "
        f"documentation/design/source-pipeline-tool.md."
    )
