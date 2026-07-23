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
| `release` | The upstream Fedora release this package was imported from |

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

| Location | What it represents |
| -------- | ------------------ |
| `metadata/<package>.json` → `release` | Base release without dist tag (Fedora baseline for imported packages; initial base for natives) |
| Spec `Release:` | The release used for the Hummingbird build (includes `%{?dist}` / `.hum1`, and may include local `.N` rebuild suffixes) |

### Fedora-imported packages

For packages with a Fedora `source`:

- `release` is set on import and refreshed on `dist_git.py update` / `sync` from the upstream
  Fedora package (dist suffixes like `.fc42` are stripped).
- Do **not** change metadata `release` during local rebuilds or backports. Rebuilds bump only the
  spec `Release:` line; metadata continues to record the Fedora baseline so `rebuild` can compute
  the next `.N` suffix correctly.
- When Fedora’s baseline is `Release: %autorelease`, metadata `release` stores the **resolved**
  numeric release from MDAPI (not the literal `%autorelease`). Import/update replace `%autorelease`
  in the local spec with that value plus `%{?dist}`. For rebuilds that still see `%autorelease`,
  follow [Rebuilding Packages](rebuilding-packages.md).

### Native packages

For Hummingbird-native packages (`modification_status: "native"`, no `source` field):

- There is no Fedora upstream release. Set `release` to a base value such as `1` or `0.1` when
  adding the package (no dist tag — same convention as Fedora-imported packages).
- The `.hum1` suffix comes from the spec `Release:` line via `%{?dist}` (for example
  `Release: 1%{?dist}`), not from metadata `release`.
- `dist_git.py rebuild` does not treat metadata `release` as an upstream Fedora baseline for
  native packages (it only uses that baseline when a `source` field is present).

### Spec `Release:` vs metadata `release`

No-change rebuilds and reverse-dependency rebuilds change the **spec** `Release:` only. See
[Rebuilding Packages](rebuilding-packages.md). When Fedora updates land later, the spec `Release:`
is replaced from upstream; metadata `release` is updated to match the new Fedora baseline.

## When not to update these fields

Most day-to-day package work changes the spec, sources, or patches — not metadata
`modification_status`, `modification_reason`, or `release`. Leave those fields alone unless the
package's relationship to Fedora actually changed.

| Situation | Do not change | Why |
| --------- | ------------- | --- |
| Native package gets a CVE patch, backport, or other source edit | `modification_status`, `modification_reason` | Status stays `native`. There is no Fedora auto-update to block, so do not switch to `modified` or add a `modification_reason`. |
| No-change rebuild (spec `Release:` bump only) | `modification_status`, `modification_reason`, metadata `release` | Rebuilds are ephemeral. Status stays `clean` (or whatever it was); metadata `release` still records the Fedora baseline. |
| Fedora-imported package gets a local patch or backport | metadata `release` | Mark the package `modified` with a reason, but keep metadata `release` as the last Fedora baseline. Only bump the **spec** `Release:` if needed. |
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
