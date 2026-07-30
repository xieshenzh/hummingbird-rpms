---
title: Package Metadata Fields
weight: 54
aliases: [/l/package-metadata-fields]
---

## Overview

Each package has a metadata file at `metadata/<package>.json`. Two fields control how
Hummingbird treats local changes and rebuilds relative to Fedora:

| Field | Purpose |
| ----- | ------- |
| `modification_status` | Whether the package may be auto-updated from Fedora |
| `release` | Base release without dist tag — Fedora/rawhide baseline, or a local base (natives / ahead-of-Fedora) |

This page is the canonical reference for configuring those fields. For day-to-day workflows
(marking packages, rebuilding, importing), see the related docs at the end.

## `modification_status`

`modification_status` records whether a package still matches its Fedora upstream import, has
local source-level changes, or is Hummingbird-native (not from Fedora).

| Status     | Meaning                                      | Auto-updates from Fedora |
| ---------- | -------------------------------------------- | ------------------------ |
| `clean`    | Unmodified Fedora import                     | Allowed                  |
| `modified` | Local changes (patches, spec modifications)  | Blocked                  |
| `native`   | Hummingbird-native package (not from Fedora) | Blocked                  |

### When to use each value

- **`clean`**: Default for packages imported from Fedora with no local source changes. Automatic
  Fedora updates are allowed. Conflicts with `modification_reason` (must be absent).
- **`modified`**: Use after backports, custom patches, or other spec/source edits that must not be
  overwritten by an automatic update. Requires `modification_reason`.
- **`native`**: Required for packages that are not imported from Fedora. These packages have no
  `source` / `branch` / `sha` fields. Conflicts with `modification_reason` (must be absent).

Imports set the status automatically (`clean` for Fedora imports, `native` for Hummingbird-native
imports). After local source changes, set status with `dist_git.py mark-modified` — see
[Package Modification Tracking](package-modification-tracking.md).

Rebuilds that only bump the spec `Release:` line do **not** change `modification_status`.
Release-only changes are ephemeral and are ignored when deciding whether a package is modified
versus clean.

### `modification_reason`

When `modification_status` is `modified`, `modification_reason` is required. It should be a short
explanation of why the package cannot be auto-updated (for example, a CVE backport or a custom
spec change). Clear the reason when marking the package clean again.

`modification_reason` applies only to `modified` packages. Do not add it for `clean` or `native`
packages — see [When not to update these fields](#when-not-to-update-these-fields).

## `release`

The `release` field in metadata is **not** the same as the `Release:` line in the package spec.
It is overloaded: the same field stores one of two kinds of base release (always without a dist
tag such as `.fc42` or `.hum1`):

1. **Fedora/rawhide base** — the upstream Fedora release this package was imported or last synced
   from (dist suffix stripped). Used while Hummingbird still tracks a Fedora build of the current
   version.
2. **Local base** — a Hummingbird-chosen base for packages with no Fedora upstream (`native`), or
   for Fedora-imported packages that Hummingbird has version-bumped ahead of Fedora. In the
   ahead-of-Fedora case this is typically `0.1` — a locally invented placeholder so a later Fedora
   import with `Release >= 1` sorts higher; it is **not** a value confirmed from an actual Fedora
   build.

`dist_git.py rebuild` uses this base (when a Fedora `source` is present) to compute the next `.N`
micro-bump on the spec `Release:` line.

| Location | What it represents |
| -------- | ------------------ |
| `metadata/<package>.json` → `release` | Base release without dist tag (Fedora/rawhide baseline **or** local base — see above) |
| Spec `Release:` | The release used for the Hummingbird build (includes `%{?dist}` / `.hum1`, and may include local `.N` rebuild suffixes) |

### Who writes metadata `release`

| Writer | When | Value stored |
| ------ | ---- | ------------ |
| `dist_git.py import` / `update` / `sync` | Fedora import or refresh | Resolved Fedora release with dist suffix stripped (e.g. `5` from `5.fc42`) |
| Native package creation | Adding a package not from Fedora | Initial local base (e.g. `1` or `0.1`) |
| `check_upstream_versions.py` | Local upstream version bump (`check --update`) | Resolved base from the updated spec — typically the local `0.1` placeholder, not a Fedora-confirmed release |

### Fedora-imported packages

For packages with a Fedora `source`:

- `release` is set on import and refreshed on `dist_git.py update` / `sync` from the upstream
  Fedora package (dist suffixes like `.fc42` are stripped). That is the Fedora/rawhide base.
- When `check_upstream_versions.py check --update` bumps the package ahead of Fedora, it resets
  the spec to `Release: 0.1%{?dist}` (unless `%autorelease`) and writes that local base (`0.1`)
  into metadata `release`. Until Fedora ships the same version and `update` / `sync` runs again,
  metadata no longer holds a Fedora-confirmed baseline.
- Do **not** change metadata `release` during local rebuilds or backports. Rebuilds bump only the
  spec `Release:` line; metadata continues to record the current base (Fedora or local) so
  `rebuild` can compute the next `.N` suffix correctly.
- When Fedora’s baseline is `Release: %autorelease`, metadata `release` stores the **resolved**
  numeric release from MDAPI (not the literal `%autorelease`). Import/update replace `%autorelease`
  in the local spec with that value plus `%{?dist}`. For rebuilds that still see `%autorelease`,
  follow [Rebuilding Packages](rebuilding-packages.md).

### Native packages

For Hummingbird-native packages (`modification_status: "native"`, no `source` field):

- There is no Fedora upstream release. Set `release` to a local base such as `1` or `0.1` when
  adding the package (no dist tag — same storage convention as Fedora-imported packages).
- The `.hum1` suffix comes from the spec `Release:` line via `%{?dist}` (for example
  `Release: 1%{?dist}`), not from metadata `release`.
- `dist_git.py rebuild` does not treat metadata `release` as an upstream Fedora baseline for
  native packages (it only uses that baseline when a `source` field is present).

### Spec `Release:` vs metadata `release`

No-change rebuilds and reverse-dependency rebuilds change the **spec** `Release:` only. See
[Rebuilding Packages](rebuilding-packages.md). When a Fedora update for the same version lands
later, `dist_git.py update` / `sync` replaces the spec `Release:` from upstream and restores
metadata `release` to the new Fedora/rawhide baseline.

## When not to update these fields

Most day-to-day package work changes the spec, sources, or patches — not metadata
`modification_status`, `modification_reason`, or `release`. Leave those fields alone unless the
package's relationship to Fedora actually changed.

| Situation | Do not change | Why |
| --------- | ------------- | --- |
| Native package gets a CVE patch, backport, or other source edit | `modification_status`, `modification_reason` | Status stays `native`. There is no Fedora auto-update to block, so do not switch to `modified` or add a `modification_reason`. |
| No-change rebuild (spec `Release:` bump only) | `modification_status`, `modification_reason`, metadata `release` | Rebuilds are ephemeral. Status stays `clean` (or whatever it was); metadata `release` still records the current base (Fedora/rawhide or local). |
| Fedora-imported package gets a local patch or backport | metadata `release` | Mark the package `modified` with a reason, but keep metadata `release` as the current base (last Fedora baseline, or the local `0.1` placeholder if already ahead of Fedora). Only bump the **spec** `Release:` if needed. |
| Package is already `modified` and you add another local change | `modification_status` | Leave status as `modified`. Update `modification_reason` only if the existing reason no longer describes why auto-updates must stay blocked. |
| Upstream Fedora update lands and you want auto-updates again | (do not leave stale fields) | Mark clean (clears `modification_reason`). Do not hand-edit `release`; let `dist_git.py update` refresh version/release from Fedora. |

### Examples

**Native package + CVE fix:** Edit the spec and add the patch. Keep:

```json
{
  "modification_status": "native",
  "release": "1",
  "version": "1.3.0"
}
```

Do not add `modification_reason`, and do not change status to `modified`.

**Fedora package + CVE backport:** After the patch, mark modified so auto-updates stay blocked:

```bash
./ci/dist_git.py mark-modified <package> --modified \
  --reason "Backport CVE-2024-12345"
```

Do not edit metadata `release` as part of that change.

**Rebuild only:** Use `dist_git.py rebuild` (or bump the spec `Release:`). Leave metadata
`modification_status` and `release` unchanged.

## Related documentation

- [Package Modification Tracking](package-modification-tracking.md) — mark packages modified/clean,
  check status, validation, and update hooks
- [Rebuilding Packages](rebuilding-packages.md) — bump spec `Release:` for no-change rebuilds and
  backports
- [Adding Native Packages](adding-native-packages.md) — create metadata for packages not from Fedora
- [Updating Dist-git Packages](updating-dist-git-packages.md) — import and update from Fedora
