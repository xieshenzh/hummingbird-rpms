---
title: Package Modification Tracking
weight: 55
aliases: [/l/package-modification-tracking]
---

## Overview

The RPMs repository tracks whether packages have been locally modified from their Fedora upstream
source. This tracking prevents automatic updates from overwriting local changes like backported
patches or custom modifications.

## Modification Status

Each package metadata file (`metadata/<package>.json`) has a `modification_status` of `clean`,
`modified`, or `independent`. That field (and related `modification_reason` / `release` configuration)
is documented in [Package Metadata Fields](package-metadata-fields.md). This page covers the
workflows for checking status, marking packages, viewing diffs, and configuring update hooks.

An optional `track_upstream` string field controls whether a package is checked by
`check_upstream_versions.py` for new upstream releases (via release-monitoring.org). Set it to
`"latest"` to track the latest version, or to a version prefix like `"1.26"` to constrain updates to
that series. Its presence enables tracking; omit the field to disable it. The `check` subcommand
only checks packages with `track_upstream` set when no explicit package arguments are given. The
`list` subcommand shows all packages regardless of this field.

## Checking Package Status

View a package's modification status:

```bash
jq .modification_status metadata/<package>.json
```

View reason for modification (if modified):

```bash
jq .modification_reason metadata/<package>.json
```

List all modified packages:

```bash
for f in metadata/*.json; do
  status=$(jq -r .modification_status "$f" 2>/dev/null)
  if [ "$status" = "modified" ]; then
    pkg=$(basename "$f" .json)
    reason=$(jq -r .modification_reason "$f" 2>/dev/null)
    echo "$pkg: $reason"
  fi
done
```

## Viewing Package Differences

To see what changes exist in a modified package compared to upstream Fedora:

```bash
# Show full diff for a package
./ci/dist_git.py diff bash

# Show summary statistics
./ci/dist_git.py diff bash --stat

# Show only which files changed
./ci/dist_git.py diff bash --name-only

# Show raw diff (includes Release: bumps and whitespace)
./ci/dist_git.py diff bash --raw

# Diff all modified packages
./ci/dist_git.py diff --all
```

**What's shown:**

- By default, the diff ignores Release: number changes (no-change rebuilds)
- Trailing whitespace and blank line changes are ignored
- Use `--raw` to see absolutely everything, including Release: bumps

**Package types:**

- **Modified packages**: Shows the differences
- **Clean packages**: Shows nothing (useful for verification)
- **Independent packages**: Skips with message "no upstream to diff against"

## Marking Packages

### Mark as Modified

Use this when you make local changes to a package (backports, custom patches, etc.):

```bash
./ci/dist_git.py mark-modified <package> --modified \
  --reason "Brief explanation of why"
```

Examples:

```bash
# After backporting a patch
./ci/dist_git.py mark-modified gcc --modified \
  --reason "Backport CVE-2024-12345 fix from upstream"

# After custom spec change
./ci/dist_git.py mark-modified systemd --modified \
  --reason "Add custom service unit for Hummingbird"
```

The reason field is **required** and should be concise but descriptive. It helps future maintainers
understand why the package can't be auto-updated.

### Mark as Clean

Use this to re-enable automatic updates after confirming your changes are no longer needed (e.g.,
the fix landed in Fedora):

```bash
./ci/dist_git.py mark-modified <package> --clean
```

This removes the `modified` status and allows the package to receive automatic updates from Fedora
again.

### Configure Upstream Tracking

Use `set-upstream` to configure upstream tracking settings for a package. Each flag independently
sets or clears one metadata field. At least one flag is required; omitted flags leave their fields
untouched.

```bash
./ci/dist_git.py set-upstream <package> [flags]
```

| Flag                     | Sets field                            | Clears with          |
| ------------------------ | ------------------------------------- | -------------------- |
| `--track-version latest` | `track_upstream: "latest"`            | `--no-track-version` |
| `--track-version VER`    | `track_upstream: "VER"`               | `--no-track-version` |
| `--project-id ID`        | `release_monitoring_project_id` (int) | `--no-project-id`    |
| `--project-id NAME`      | `release_monitoring_project_id` (str) | `--no-project-id`    |

Each set/clear pair is mutually exclusive (can't pass `--track-version` and `--no-track-version`
together).

**Examples:**

```bash
# Enable upstream version tracking (any version)
./ci/dist_git.py set-upstream bash --track-version latest

# Disable upstream version tracking
./ci/dist_git.py set-upstream bash --no-track-version

# Set upstream name with version constraint for versioned packages
./ci/dist_git.py set-upstream golang1.26 --project-id golang --track-version 1.26

# Set release-monitoring.org project ID (integer)
./ci/dist_git.py set-upstream python3.11 --track-version 3.11 --project-id 13254

# Remove project ID (reverts to RPM name lookup)
./ci/dist_git.py set-upstream python3.11 --no-project-id

# Combine multiple flags in one call
./ci/dist_git.py set-upstream golang1.26 \
  --track-version 1.26 --project-id 13254
```

**Metadata fields:**

| Field                           | Description                                                       | Example                                  |
| ------------------------------- | ----------------------------------------------------------------- | ---------------------------------------- |
| `track_upstream`                | `"latest"` or version prefix to constrain updates                 | `"latest"`, `"1.26"`                     |
| `release_monitoring_project_id` | Anitya project ID (int) or upstream name (str)                    | `13254`, `"golang"`                      |
| `version_suffix_strip`          | Suffix to strip from Anitya-reported versions                     | `"-RELEASE"`                             |
| `upstream_version_transform`    | Named transform from Anitya version to RPM scheme                 | `"openjdk_to_rpm"`                       |
| `source_availability_check`     | Named source checker to HEAD-probe before selecting a version     | `"openjdk_osci"`                         |

These fields affect two systems:

- **`dist_git.py update`**: When `track_upstream` is a version prefix, skips upstream versions that
  don't match. For example, `track_upstream: "1.26"` allows `1.26`, `1.26.0`, `1.26.3` but rejects
  `1.27.0`.
- **`check_upstream_versions.py`**: When `release_monitoring_project_id` is an integer, queries the
  v2 API directly by Anitya project ID. When it is a string, queries release-monitoring.org using
  that name instead of the RPM package name (e.g., looks up `golang` instead of `golang1.26`). When
  absent, uses the RPM package name. When `track_upstream` is a version prefix, filters the reported
  upstream versions to only those matching the prefix. Only packages with `track_upstream` set are
  checked by `check_upstream_versions.py check` when no explicit package arguments are given.

Find release-monitoring.org project IDs by searching on <https://release-monitoring.org>.

The project ID can be combined with a version prefix to filter versions returned by the project ID
lookup:

```json
{
  "release_monitoring_project_id": 13254,
  "track_upstream": "3.11"
}
```

Some upstream projects tag releases with a suffix that is not part of the RPM version (e.g.,
`swift-6.3.3-RELEASE`). After Anitya strips the version prefix, the reported version still contains
the suffix (`6.3.3-RELEASE`), which is incompatible with RPM's `Version:` field (hyphens are not
allowed). Use `version_suffix_strip` to remove it before comparison and update:

```json
{
  "release_monitoring_project_id": 21267,
  "version_suffix_strip": "-RELEASE"
}
```

Some upstream projects publish source tarballs independently of tag creation, and Anitya may report
a version before the tarball is available. Use `source_availability_check` to name a checker
function (registered in `SOURCE_AVAILABILITY_CHECKERS` in `check_upstream_versions.py`) that
HEAD-probes the source URL before selecting a version. Versions whose source returns 404 are
skipped in favour of the next available version. Available checkers:

- `openjdk_osci` — probes `https://openjdk-sources.osci.io/openjdk{feature}/openjdk-{version}.tar.xz`

```json
{
  "release_monitoring_project_id": 369281,
  "track_upstream": "21",
  "upstream_version_transform": "openjdk_to_rpm",
  "source_availability_check": "openjdk_osci"
}
```

### `version_from_ref` (fixed-ref, no-tagged-release packages)

Most gorget `source-pipeline.yaml` fetch steps are version-templated (`ref: "v${VERSION}"`), so
`dist_git.py update` can invoke gorget with `metadata/<package>.json`'s own `version` directly. A
few packages instead pin a fixed git-snapshot commit with no tagged upstream release at all (gcc,
glibc, libyuv, php-patchwork-jsqueeze, vim) -- their `version` field doesn't change when the pinned
commit does, so it can't drive gorget's `--version` argument.

`version_from_ref` tells `dist_git.py update` how to derive gorget's `--version` string from a
newly-pinned commit, instead of requiring someone to compute it by hand every time (the previous
process, documented as a manual step in each such package's `source-pipeline.yaml` comments).

Currently one `type` is supported:

- **`commit-date`** (used by gcc): `"<metadata.json's version>-<commit's own YYYYMMDD date>"`, where
  the date comes from `git log -1 --format=%cd --date=format:%Y%m%d <ref>` run against the newly-pinned
  commit.

```json
{
  "version_from_ref": {"type": "commit-date"}
}
```

**Currently gcc-only.** `dist_git.py update` only fully automates a fixed-ref pin refresh for gcc's
simple case (a full 40-char commit SHA lives directly in a spec `%global`). The other four
packages pin via a `git describe`-style string (e.g. glibc's `%{glibcsrcdir}` macro,
`glibc-2.43-47-gbc95068f5f`) whose trailing hash is only a 10-character abbreviation -- resolving
that back to a full SHA needs a local clone containing the commit object, real extra network work
with its own failure modes (ambiguous hashes, upstream unavailability), not yet implemented (see
HUM-4621's "Remaining work"). For those four, `dist_git.py update` detects when the pipeline's
pinned `ref:` no longer matches the merged spec and refuses to auto-commit -- it's routed to a
draft, `no-test`-labeled MR for manual resolution instead, the same way a real git merge conflict
is, rather than silently committing a `sources` file gorget was never actually run against.

## Per-Package Update Hooks

When `check_upstream_versions.py check --update` updates a package, by default it sets `Version:` to
the new upstream version and `Release:` to `0.1%{?dist}` (unless `%autorelease` is used), adds a
changelog entry, and downloads new sources from the URLs declared in the spec. Some packages need
custom logic (e.g. generating stripped tarballs or patching macro-based version lines). A
per-package hooks file lets you override or extend these default phases without changing
`check_upstream_versions.py` itself.

### Hooks file location

```text
metadata/<package>.update-hooks.yaml
```

For example, `metadata/nodejs25.update-hooks.yaml`.

### Hook phases

The YAML file supports three optional keys. Each value is a shell command string executed with
`bash -eo pipefail -c` in the package directory as the working directory.

| Phase              | Behaviour                                                                                                                                                                               |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `update_spec`      | **Replaces** the default update that sets `Version:` to the new upstream version and `Release:` to `0.1%{?dist}`. A changelog entry is still added automatically.                       |
| `download_sources` | **Replaces** the default URL-based source download. Must print one filename per line to stdout for files to upload to the lookaside cache. Redirect any other output to stderr (`>&2`). |
| `post_update`      | **Additive** — runs after spec + sources are ready. No default equivalent.                                                                                                              |

Omitting a phase means the default logic runs for that phase. Packages without a hooks file behave
identically to before.

Unknown phase keys in the YAML cause a `ValueError` (fail-fast).

### Environment variables

Every hook receives these environment variables:

| Variable              | Example                                  |
| --------------------- | ---------------------------------------- |
| `UPDATE_PACKAGE`      | `nodejs25`                               |
| `UPDATE_OLD_VERSION`  | `25.6.1`                                 |
| `UPDATE_NEW_VERSION`  | `25.8.2`                                 |
| `UPDATE_SPEC_FILE`    | `/home/rpms/rpms/nodejs25/nodejs25.spec` |
| `UPDATE_PACKAGE_DIR`  | `/home/rpms/rpms/nodejs25`               |
| `UPDATE_SOURCES_FILE` | `/home/rpms/rpms/nodejs25/sources`       |
| `UPDATE_ROOT_DIR`     | `/home/rpms`                             |

### Example

See [`metadata/nodejs25.update-hooks.yaml`](../../../metadata/nodejs25.update-hooks.yaml) for a
working example that uses all three hook phases.

## How Auto-Updates Work

The `./ci/dist_git.py update` command (used by automation) checks modification status before
updating packages:

- **clean packages**: Updated automatically when new Fedora versions are available
- **modified packages**: Automatically merged with upstream changes (conflicts create draft MRs)
- **independent packages**: Update blocked (not sourced from Fedora)
- **version-constrained packages**: Skipped if upstream version doesn't match `track_upstream`
  prefix

To force-update a modified package (discarding local changes):

```bash
./ci/dist_git.py sync <package>
```

The `sync` command bypasses the modification check and force-updates to the latest upstream version.
After syncing, the package is automatically marked clean.

## Resolving Merge Conflicts

When modified packages are updated from Fedora, `dist_git.py update` attempts to automatically merge
local changes with the new upstream version using git's three-way merge. When conflicts occur, the
update still succeeds but creates a commit with conflict markers, and the automation files a draft
merge request labeled with `CONFLICT:` for manual resolution.

### Understanding Conflict Markers

Git uses this conflict marker structure:

```text
<<<<<<< HEAD
Fedora's version (new upstream)
=======
Hummingbird's local modifications
>>>>>>> hummingbird-local
```

**ALL THREE markers must be removed** for a clean resolution.

### Update branch/MR structure

MRs are created on branches following the pattern:

```text
chore/dist-git-update-PACKAGENAME
```

These branches are automatically created by the `dist_git_update` GitLab schedule. If they have
conflicts, they result in draft MRs with:

- Title prefix: `CONFLICT: chore(rpms): Update ...`
- Description listing the conflicting files
- `no-test` label to skip CI tests (saves resources since conflicts need manual resolution)

### Resolution Process

1. **Check out the conflict branch:**

   ```bash
   git fetch origin
   git checkout origin/chore/dist-git-update-PACKAGENAME
   ```

2. **Examine the conflict:**

   ```bash
   # Find all files with conflict markers
   git grep -nE '^<{7} .+|^={7}$|^>{7} .+' -- rpms/PACKAGENAME/

   # View the specific conflict
   git show HEAD:rpms/PACKAGENAME/PACKAGENAME.spec | grep -B5 -A10 "^<<<<<<< HEAD"
   ```

3. **Understand the local changes:**

   ```bash
   # Review commit history to understand why changes were made
   git log --oneline -- rpms/PACKAGENAME/
   git log -p -- rpms/PACKAGENAME/  # With diffs

   # Check the modification reason
   jq -r .modification_reason metadata/PACKAGENAME.json
   ```

   Understand the context for correct resolution:
   - What was the original purpose of the local change? Is it transient or permanent?
   - Is it a workaround for a bug, a security patch, or a configuration difference?
   - Does it affect other packages (e.g., nss builds nspr as a subpackage)?
   - Check spec file comments (e.g., NOTE: comments) for packaging details

   Decide which version to accept:
   - **Accept HEAD (Fedora)** for: release number lags, fixed workarounds that Fedora improved or
     addressed differently
   - **Keep hummingbird-local** for: security patches not in Fedora, FIPS requirements, critical
     fixes, and other permanent modifications
   - **Merge both** for: test skip lists, independent changes that don't conflict logically
   - **When in doubt:** Accept Fedora's version for packaging metadata (Release:, subpackage
     versions), keep Hummingbird's version for functional changes (patches, dependencies, build
     options)

4. **Resolve the conflict:** Edit the file to choose the appropriate version (HEAD,
   hummingbird-local, or merge both). Verify no markers remain:

   ```bash
   git grep -nE '^<{7} .+|^={7}$|^>{7} .+' -- rpms/PACKAGENAME/
   ```

5. **Validate the resolution:** Check that local modifications are preserved:

   ```bash
   # Check the diff against upstream (works on working tree, staging not required)
   ./ci/dist_git.py diff PACKAGENAME

   # Compare with previous modification commits to verify
   git log -p -- rpms/PACKAGENAME/
   ```

   The diff should show only the intended local modifications (ignoring Release: bumps). This
   confirms the merge preserved your changes correctly. Note: `dist_git.py diff` compares the
   filesystem working tree against upstream, so it works before or after staging.

6. **Amend the commit:** Record the original conflicted commit SHA, then amend:

   ```bash
   # Record the original conflicted commit SHA
   ORIGINAL_SHA=$(git rev-parse HEAD)

   # Stage the resolved files and amend the commit
   git add rpms/PACKAGENAME/
   git commit --amend -m "$(git log -1 --format=%B | head -n -1)

   Conflicted-Update: $ORIGINAL_SHA"
   ```

   This preserves the original commit message while adding a `Conflicted-Update:` trailer
   that records which commit contained the conflict markers. This helps track the resolution
   history and can be useful for auditing or debugging later.

7. **Push the resolution:**

   ```bash
   git push origin HEAD:chore/dist-git-update-PACKAGENAME --force-with-lease --push-option merge_request.unlabel=no-test
   ```

   This removes the `no-test` label from the MR, which triggers CI tests to run and verifies the
   resolution works correctly. Some developers might have `origin` as read-only remote, and a
   different writable remote (e.g. `originw`).

### Common Conflicts

#### nss: Subpackage Release Numbers

The `nss` package builds `nspr` as a subpackage with its own release number offset.

**BACKGROUND:**

- `nss` builds both `nss` and `nspr` RPMs from the same source
- `nspr_release` uses an offset (`%[%baserelease+n]`) to avoid NVR clashes
- The spec file NOTE explains: reset to 1 when `nspr_version` changes, increment when only `nss`
  changes
- Fedora manages these offsets in their ecosystem to prevent conflicts

**CONFLICT EXAMPLE:**

```text
<<<<<<< HEAD
%global nspr_release %[%baserelease+3]
=======
%global nspr_release %[%baserelease+1]
>>>>>>> hummingbird-local
```

**REASONING:** When updating to a new upstream `nss` version from Fedora:

- Accept Fedora's `nspr_release` offset (HEAD) - they manage NVR clashes
- Our local offset was specific to Hummingbird rebuilds
- New upstream version should reset to Fedora's packaging values
- Don't try to "calculate" what it should be - trust Fedora's packaging

**RESOLUTION:** Accept HEAD (Fedora's value)

## Special Case: Rebuild-Only Changes

Release-only changes (no-change rebuilds) are automatically ignored by the modification detection
logic. This means:

- Bumping `Release: 3%{?dist}` → `Release: 3.1%{?dist}` does **not** mark the package as modified
- The package can still receive automatic Fedora updates
- The Release bump will be preserved if the update doesn't change the upstream Release field

You **do not need** to mark packages as modified for rebuild-only changes, unless you want to
explicitly prevent automatic updates for other reasons.

## CI Validation

The CI pipeline validates modification status consistency using `make check`, which runs:

```bash
./ci/validate_package_modifications.py --all
```

This validation ensures:

1. All packages have a `modification_status` field
2. The value is one of: `clean`, `modified`, `independent`
3. If `track_upstream` is present, it must be a string (`"latest"` or a version prefix)
4. Modified packages have a `modification_reason`
5. Independent packages do not have source/branch/sha fields (Hummingbird-independent only)
6. Git commit history matches the declared modification status

The validation runs on every merge request and push to main, failing the build if metadata is
inconsistent.

For local development, run the full validation:

```bash
./ci/validate_package_modifications.py --all
```

Or validate specific packages:

```bash
./ci/validate_package_modifications.py bash glibc gcc
```

### Validation Modes

The validation script has two modes:

**Fast mode (default)**: Checks git commit history patterns

```bash
./ci/validate_package_modifications.py --all
```

This validates that all commits since the last Sync follow standard patterns (have Upstream:
trailers). Runs in less than a minute for all packages.

**Thorough mode**: Clones upstream repos and compares filesystems

```bash
./ci/validate_package_modifications.py --all --thorough
```

This performs full filesystem comparisons with upstream Fedora repositories. Slow and unreliable
(hundreds of upstream dist-git clones) but authoritative - validates actual state regardless of git
commit history.

For CI and daily development, fast mode is sufficient. Use thorough mode when:

- Debugging discrepancies between metadata and actual state
- Auditing the entire repository for hidden modifications
- Investigating why a package can't be updated

## Workflow Examples

### Backporting a Patch

1. Add patch file and modify spec (see [Rebuilding Packages](../rebuilding-packages))
2. Commit the changes
3. **Mark as modified:**

   ```bash
   ./ci/dist_git.py mark-modified dnf5 --modified \
     --reason "Backport reproducible build fix (upstream PR#2522)"
   ```

4. Package is now protected from automatic Fedora updates

### Re-enabling Auto-Updates

When your backported fix lands in Fedora:

1. Verify the fix is in the latest Fedora version:

   ```bash
   ./ci/dist_git.py update dnf5  # This will fail with "modified" error
   ```

2. Mark the package clean:

   ```bash
   ./ci/dist_git.py mark-modified dnf5 --clean
   ```

3. Update from Fedora:

   ```bash
   ./ci/dist_git.py update dnf5  # Now succeeds
   ```

### Importing New Packages

When importing packages, modification status is set automatically:

```bash
# Fedora package → marked as "clean"
./ci/dist_git.py import fedora/neofetch

# Hummingbird-independent package → marked as "independent"
./ci/dist_git.py import hummingbird/custom-tool
```

No manual marking needed for imports.

## Troubleshooting

### CI Fails: "Missing modification_status field"

This means a metadata file is missing the required field. This means that the package was not
imported properly.

### CI Fails: "Marked as clean but package has modifications"

The package has local changes but metadata says it's clean. To fix:

1. Check what changed:

   ```bash
   git log -p -- rpms/<package>/
   ```

2. Mark as modified with the appropriate reason:

   ```bash
   ./ci/dist_git.py mark-modified <package> --modified --reason "..."
   ```

### CI Fails: "Marked as modified but package is actually clean"

The package has no local changes but is marked modified. To fix:

1. Verify it's actually clean:

   ```bash
   ./ci/dist_git.py update <package>  # Check if upstream matches
   ```

2. If confirmed clean, remove the modified status:

   ```bash
   ./ci/dist_git.py mark-modified <package> --clean
   ```

### Update Blocked: "Cannot auto-update `<package>`"

This is expected for modified packages. Options:

1. **Wait for fix to land in Fedora**, then mark clean and update
2. **Force-sync** to discard local changes:

   ```bash
   ./ci/dist_git.py sync <package>
   ```

3. **Keep blocked** if the local changes are still needed

## Related Documentation

- [Package Metadata Fields](package-metadata-fields.md) - `modification_status` and `release`
  configuration
- [Rebuilding Packages](../rebuilding-packages) - How to rebuild and backport patches
- [Updating Dist-git Packages](../updating-dist-git-packages) - How automatic updates work
