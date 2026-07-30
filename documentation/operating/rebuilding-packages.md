---
title: Rebuilding Packages
weight: 50
aliases: [/l/rebuilding-packages]
---

> **AI Agent Note:** When asked to rebuild packages, use the `rebuild` command:
> `./ci/dist_git.py rebuild <package> --reason "<reason>"`. For rebuilding reverse dependencies
> (e.g., "rebuild all Go packages"), use `rebuild-rev-deps <package> --reason "<reason>"`.
> Always ask the user for a ticket link or explanation first to use as the reason. If the command
> fails with "uses macros in Release field", see the "Packages requiring manual rebuild" section
> below for instructions.

## Overview

This document covers three scenarios for triggering package builds:

1. **No-change rebuild**: Bump the Release field to rebuild with identical sources (e.g., to fix a
   faulty published RPM or pick up toolchain changes).

2. **Rebuilding reverse dependencies**: Rebuild all packages that depend on a changed package (e.g.,
   rebuild all Go packages when golang updates).

3. **Backporting a patch**: Add an upstream patch that hasn't yet landed in Fedora to fast-track a
   fix or feature.

All scenarios use the `.N` release suffix pattern to ensure our builds sort higher than the
upstream Fedora release while remaining lower than the next upstream version.

## No-Change Rebuild

### Using the rebuild command (recommended)

The `rebuild` command automates the Release field bump:

```bash
# Rebuild a single package
./ci/dist_git.py rebuild <package> --reason "<reason>"

# Rebuild multiple packages (one commit per package)
./ci/dist_git.py rebuild <package1> <package2> ... --reason "<reason>"

# Rebuild all packages (one commit per package)
./ci/dist_git.py rebuild --all --reason "<reason>"

# Rebuild all packages except specific ones (requires --all)
./ci/dist_git.py rebuild --all --exclude <pkg1>,<pkg2> --reason "<reason>"
```

Examples:

```bash
# Single package
./ci/dist_git.py rebuild ncurses --reason "published multiple times with different hashes"

# Multiple packages
./ci/dist_git.py rebuild grep unzip sed --reason "fix faulty builds"

# All packages (useful after toolchain updates)
./ci/dist_git.py rebuild --all --reason "toolchain update: GCC 15"

# All packages except a few already rebuilt earlier in the same rollout
./ci/dist_git.py rebuild --all --exclude glibc,gcc,llvm --reason "toolchain update: GCC 15"
```

`--exclude` takes a comma-separated list of package names and only works together with
`--all` — it is rejected when combined with an explicit package list, since you can simply
omit the packages you don't want to rebuild in that case.

The command:

- Automatically bumps the Release field using the `.N` suffix pattern
- Handles `%autorelease` by resolving and replacing with explicit values
- Preserves macros in the Release field (e.g., `%{revision}`)
- Creates a properly formatted commit message (one per package with `--all`)
- Does **not** mark the package as modified (release-only changes are ephemeral)

Supports `--dry-run` to preview changes without committing:

```bash
./ci/dist_git.py --dry-run rebuild <package> --reason "test"
```

### Creating MRs for rebuild commits

After creating rebuild commits locally, use `rebuild_multi_mr.sh` to push each commit as its own
merge request (one MR per package, auto-merge enabled):

```bash
./ci/rebuild_multi_mr.sh
```

By default the script compares against `origin/main`. For local development, use `--base` to point
at a different ref:

```bash
# Use local main branch as base (useful when origin/main is not up to date)
./ci/rebuild_multi_mr.sh --base main

# Use a specific commit SHA as base
./ci/rebuild_multi_mr.sh --base abc1234

# Preview what would be created without pushing
./ci/rebuild_multi_mr.sh --dry-run

# Limit to at most N MRs
./ci/rebuild_multi_mr.sh --max-updates=5
```

The script:

- Creates a `chore/rebuild-{package}` branch per commit and pushes it
- Titles each MR `chore(rpms): Rebuild {package}: {reason}`
- Enables auto-merge on all rebuild MRs
- Leaves the current branch untouched
- Skips branches that already exist on the remote (idempotent)
- Only processes commits whose message matches `Rebuild {package}: {reason}`; other commits in the
  range are silently skipped

**Full workflow example:**

```bash
# 1. Create the rebuild commits
./ci/dist_git.py rebuild grep ncurses bash --reason "HUM-1234: toolchain update"

# 2. Preview the MRs that would be created
./ci/rebuild_multi_mr.sh --base main --dry-run

# 3. Create the MRs
./ci/rebuild_multi_mr.sh --base main
```

### Packages requiring manual rebuild

Some packages use complex macro systems that the automated rebuild command cannot handle. These
require manual editing of the spec file.

#### Macro indirection patterns

These packages define the Release field using a macro, where the macro itself contains `%{?dist}`.
The rebuild command cannot detect or manipulate these without expanding all macros, which would
break the macro system.

**nodejs packages (nodejs20, nodejs22, nodejs24, nodejs25):**

```spec
%{load:%{_sourcedir}/nodejs.srpm.macros}
%nodejs_define_version node 1:25.8.2-%{autorelease} -p
...
Release: %{node_release}
```

The `%{node_release}` macro is defined by an external macro system loaded from `nodejs.srpm.macros`.
The release component is embedded in the version definition.

**How to rebuild:** Edit the `%nodejs_define_version node` line to bump the release component (e.g.,
change `-%{autorelease}` to `-1.1` or increment existing `.N`).

**kernel-headers:**

```spec
%define specrelease 59%{?buildid}%{?dist}
...
Release: %{specrelease}
```

**How to rebuild:** Edit the `%define specrelease` line to add/increment the `.N` suffix before
`%{?buildid}`:

```spec
%define specrelease 59.1%{?buildid}%{?dist}
```

**krb5:**

```spec
%global krb5_release 4%{?dist}
...
Release: %{krb5_release}
```

**How to rebuild:** Edit the `%global krb5_release` line to add/increment the `.N` suffix:

```spec
%global krb5_release 4.1%{?dist}
```

#### Why these can't be automated

The rebuild command can handle:

- ✅ Simple numeric: `Release: 5%{?dist}`
- ✅ Macros ending with dist: `Release: %{baserelease}%{?dist}` (e.g., rpm, gcc)
- ✅ Complex macros with dist: `Release: %{?snapver:0.%{snapver}.}%{baserelease}%{?dist}`
- ✅ Content after dist: `Release: 11.1%{?dist} %{?extra_version:-e %{extra_version}}` (e.g.,
  unbound)

The rebuild command **cannot** handle:

- ❌ Macros without `%{?dist}`: `Release: %{node_release}`
- ❌ Macros where dist is inside the macro definition: `%{krb5_release}` contains `%{?dist}`

This is because detecting and manipulating macros that contain dist internally would require
expanding all macros (which changes the spec file semantically) or implementing RPM's full macro
parser.

### Manual rebuild process

If you need to rebuild manually or the automated command doesn't work for your use case, follow
these steps:

#### 1. Identify the package to rebuild

Identify the source package name and locate its spec file in `rpms/<package>/<package>.spec`.

If you have a binary RPM name, the source package name may differ. Query the Hummingbird repos to
get the source RPM name:

```bash
podman run --rm quay.io/hummingbird-ci/hummingbird-builder:latest \
  dnf5 repoquery --queryformat '%{SOURCERPM}' <binary-package> 2>/dev/null
```

Example: `ncurses-libs-6.5-8.20250614.hum1` -> SRPM `ncurses-6.5-8.20250614.hum1.src.rpm` -> spec
file at `rpms/ncurses/ncurses.spec`

#### 2. Determine the Release bump pattern

The `.N` bump suffix must always appear **immediately before** `%{?dist}`. The `%{?dist}` suffix
should always be the final component since it identifies the build environment.

| Current Pattern | Example Before                   | Example After                      |
| --------------- | -------------------------------- | ---------------------------------- |
| Simple numeric  | `Release: 3%{?dist}`             | `Release: 3.1%{?dist}`             |
| Already bumped  | `Release: 3.1%{?dist}`           | `Release: 3.2%{?dist}`             |
| With macro      | `Release: 8.%{revision}%{?dist}` | `Release: 8.%{revision}.1%{?dist}` |
| autorelease     | `Release: %autorelease`          | `Release: 1.1%{?dist}`             |

For `%autorelease`, first resolve its value using `rpmspec`, then replace with the resolved value
plus `.1`. In the Hummingbird monorepo, `%autorelease` always evaluates to `1`.

> **Note:** If the Release field is missing `%{?dist}` entirely or looks unusual (e.g., `1build1`
> instead of `1.1%{?dist}`), flag this to the user for resolution. Check the git history to
> understand the original value:
>
> ```bash
> git log -p -S "Release:" -- rpms/<package>/<package>.spec
> ```
>
> This helps determine the correct fix when a previous bump was malformed.

#### 3. Modify the spec file

Use `sed` to edit only the `Release:` line, avoiding any unintended whitespace changes that text
editors may introduce:

```bash
sed -i 's/^Release: 3%{?dist}$/Release: 3.1%{?dist}/' rpms/<package>/<package>.spec
```

Verify the change with `git diff` before committing:

```bash
git diff rpms/<package>/<package>.spec
```

The diff should show only the Release line change:

```diff
- Release: 3%{?dist}
+ Release: 3.1%{?dist}
```

> **Important:** Only modify the Release line. Do not introduce any other changes such as whitespace
> fixes or trailing newline modifications. If the diff shows additional changes, reset and retry
> with `sed`.
>
> **Important:** Do not change the `release` field in `metadata/<package>.json` during local
> rebuilds or backports. That field is the current base release (Fedora/rawhide baseline, or a
> local base such as `0.1` when ahead of Fedora); see
> [Package Metadata Fields](package-metadata-fields.md).

#### 4. Verify the bump is correct

Use `rpm --eval` to confirm the new release sorts higher than the original:

```bash
# Returns -1 if first < second (correct), 1 if first > second (wrong)
rpm --eval '%{lua:print(rpm.vercmp("3.hum1", "3.1.hum1"))}'
# Expected output: -1
```

#### 5. Commit the change

Use this commit message format:

```text
Rebuild <package>: <reason>

<ticket link or explanation>
```

Example:

```text
Rebuild ncurses: published multiple times with different hashes

HUM-1234
```

#### 6. Verify the commit

After committing, verify only the Release line was changed:

```bash
git show --stat HEAD
```

Expected output should show exactly 1 insertion and 1 deletion:

```text
 rpms/<package>/<package>.spec | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

If the commit shows more changes, amend or reset and redo the change using `sed`.

### Important notes about rebuilds

#### Modification status

Rebuilds **do not** change a package's `modification_status`. Spec `Release:` bumps are ephemeral
and do not make a package `modified` versus `clean`. See
[Package Metadata Fields](package-metadata-fields.md) for how `modification_status` and metadata
`release` relate to rebuilds.

During updates, Release lines are normalized to avoid conflicts, and the automation ignores
Release-only changes, so automatic Fedora updates continue normally after a rebuild.

If you want to explicitly prevent automatic updates (e.g., you're investigating an issue), you can
manually mark the package as modified:

```bash
./ci/dist_git.py mark-modified <package> --modified --reason "Investigating build issue"
```

Note: This will block automatic Fedora updates until you mark it clean again.

## Rebuilding Reverse Dependencies

When a compiler, runtime, or toolchain package changes, you may need to rebuild all packages that
depend on it. The `rebuild-rev-deps` command automates finding and rebuilding reverse dependencies.

### When to use

Common scenarios for rebuilding reverse dependencies:

- **Golang/Python runtime updates**: When updating `golang1.26`, `python3.14`, etc.,
  rebuild all Go/Python packages
- **Toolchain changes**: When updating `gcc`, rebuild packages that BuildRequire it
- **Library ABI changes**: When a library's ABI changes, rebuild packages that BuildRequire it

### Usage

```bash
./ci/dist_git.py rebuild-rev-deps <package> --reason "<reason>"
```

The command:

1. Finds all packages that have `BuildRequires: <package>` in their spec files
2. Rebuilds each package (bumps Release field and commits)
3. Reports a summary of successful/failed rebuilds

Examples:

```bash
# Rebuild all Go packages when golang updates
./ci/dist_git.py rebuild-rev-deps golang1.26 --reason "golang 1.26.2 update"

# Rebuild all Python packages when python updates
./ci/dist_git.py rebuild-rev-deps python3.14 --reason "python 3.14.1 update"

# Rebuild packages that depend on a specific library
./ci/dist_git.py rebuild-rev-deps openssl --reason "openssl 3.4.0 update"
```

### Virtual BuildRequires (golang/python)

For virtual BuildRequires like `golang1.25`, `golang1.26`, `python3.13`, `python3.14`, the command
automatically handles translation to the actual BuildRequires target:

```bash
# These all work the same way:
./ci/dist_git.py rebuild-rev-deps golang1.26 --reason "..."
./ci/dist_git.py rebuild-rev-deps go-rpm-macros --reason "..."
```

**Important:** Only the **latest** version triggers rebuilds. This is because:

1. Multiple golang versions exist: `golang1.25`, `golang1.26`
2. They all provide the same virtual package: `Provides: golang = <version>`
3. DNF always picks the highest version to satisfy `Requires: golang`

Therefore:

- ✅ `rebuild-rev-deps golang1.26` → Rebuilds all Go packages (latest version)
- ❌ `rebuild-rev-deps golang1.25` → **Error**: Not the latest version

This ensures rebuilds only happen when the active runtime actually changes.

### Creating MRs for reverse dependency rebuilds

After creating rebuild commits, use `rebuild_multi_mr.sh` to push each commit as its own MR:

```bash
./ci/rebuild_multi_mr.sh --base main
```

See the "Creating MRs for rebuild commits" section above for full details.

### Dry-run mode

Preview which packages would be rebuilt without making changes:

```bash
./ci/dist_git.py --dry-run rebuild-rev-deps golang1.26 --reason "test"
```

This shows:

- Which packages have the BuildRequires dependency
- What the new Release values would be
- Does not commit any changes

## Backporting a Patch

Use this workflow when you need to fast-track an upstream fix or feature that hasn't yet been
released in Fedora.

### 1. Obtain the patch

Fetch the patch from the upstream repository. For GitHub PRs, append `.patch` to the PR URL:

```bash
curl -L https://github.com/<org>/<repo>/pull/<number>.patch \
  > rpms/<package>/<NNNN>-<short-description>.patch
```

Name the patch file with a numeric prefix matching the next available `PatchN:` slot in the spec
file (e.g., `0004-fix-foo.patch` if Patch1-3 already exist).

### 2. Add the patch to the spec file

Add a `PatchN:` declaration after the existing patches:

```spec
Patch3:         0003-existing-patch.patch
Patch4:         0004-fix-foo.patch
```

The patch will be applied automatically if the spec uses `%autosetup -p1`. If the spec uses explicit
`%patchN` macros, add the corresponding apply line in the `%prep` section.

### 3. Bump the Release

Follow the same `.N` suffix pattern as no-change rebuilds:

```diff
- Release: 3%{?dist}
+ Release: 3.1%{?dist}
```

### 4. Commit the patch

Use this commit message format:

```text
<package>: backport <short description>

Backport: <link to PR or commit>
<ticket link if applicable>
```

Example:

```text
dnf5: backport reproducible build sorting fix

Backport: https://github.com/rpm-software-management/dnf5/pull/2522
```

### 5. Mark package as modified

Mark the package as modified to prevent automatic Fedora updates from overwriting your backport:

```bash
./ci/dist_git.py mark-modified <package> --modified \
  --reason "Backport fix for <issue description>"
```

Example:

```bash
./ci/dist_git.py mark-modified dnf5 --modified \
  --reason "Backport reproducible build sorting fix from upstream PR#2522"
```

This ensures the package won't be automatically updated from Fedora until the backported patch lands
upstream and you explicitly mark it clean again.

### 6. Test the build locally (optional)

Build the package locally to verify the patch applies cleanly:

```bash
./ci/build_rpms.sh <package>
```

Built RPMs will be in `builds/<package>/RPMS/`.

## Related Operations

- [Excluding Packages from Images][exclude] - temporarily block faulty packages in container builds

[exclude]: https://hummingbird-project.io/l/excluding-packages-from-images
