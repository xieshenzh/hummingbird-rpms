---
title: Rebasing the Buildroot to a New Fedora Release
description: Step-by-step runbook for moving mock.cfg, the core toolchain, and the rest of the package set to a new Fedora release
weight: 65
---

> **AI Agent Note:** This document is the procedural runbook for a Fedora buildroot rebase (e.g.
> F43 -> F44). It was written after executing the F44 rebase (see
> [HUM-2018](https://redhat.atlassian.net/browse/HUM-2018) and its child stories HUM-3369,
> HUM-3370, HUM-4536, HUM-4537 for the detailed history) and should be followed, and updated, the
> next time this operation is performed (e.g. F44 -> F45). Read this whole document before
> starting; the ordering between steps matters and several steps have hard dependencies on the one
> before it.

## Overview

Periodically, Fedora ships a new stable release and Hummingbird needs to move its buildroot
(`mock/mock.cfg`) and the packages that track a specific Fedora branch (rather than `rawhide`) onto
it. This is a multi-day, multi-phase effort touching the core toolchain, CI/testing configuration,
and potentially every package in the repository (since changing the compiler/linker toolchain can
affect any build). The F44 rebase took about 2 calendar weeks.

## Pre-flight checks

Before starting, confirm:

- The target Fedora release is actually stable (not just branched) and its package repos are
  reachable from the build environment.
- Which packages currently track the outgoing release branch explicitly (i.e. `branch` is a
  versioned Fedora branch like `f43`, not `rawhide`) via:

  ```bash
  # List every package whose branch is pinned to a specific Fedora release (not rawhide)
  ./ci/dist_git.py list | xargs -I{} sh -c 'echo -n "{}: "; python3 -c "import json; print(json.load(open(\"metadata/{}.json\"))[\"branch\"])"' 2>/dev/null | grep -E ': f[0-9]+$'
  ```

  For each, decide up front whether it should move to the new branch, move to `rawhide`, or stay
  pinned (see "Packages that intentionally stay behind" below) — don't leave this undecided until
  a package fails to build.
- Whether any of those packages carry FIPS-validation-critical modifications. Packages whose
  `modification_reason` references FIPS certification should generally **not** be rebased to new
  branch content, even though they still need to be rebuilt against the new toolchain. Moving them
  would risk invalidating the FIPS-validated source. (See HUM-4989 for a related, more general
  proposal to record *why* a package is pinned in its metadata rather than relying on the
  modification reason text.)
- Whether any package whose version *is* the "which version is default" signal for an adjacent
  language/tooling ecosystem (e.g. `python-rpm-macros`) needs the same branch-pinning treatment as
  the interpreter itself — especially if that ecosystem is mid-transition to a new major version
  upstream at the same time as this rebase. See "Known gotchas: python3dist / rawhide-tracked
  toolchain-adjacent packages" below for why this matters and what already went wrong once.
- Whether to pause the routine automated dist-git sync bot for the duration of the Step 4 mass
  rebuild. See "Known gotchas: automation racing a manual mass rebuild" below for the tradeoff.

## Step 1 — Buildroot config + core toolchain (separate MRs)

Do **not** bundle the buildroot config change with the toolchain rebuilds in one MR — split them so
the buildroot config lands and validates quickly, independent of the (much slower) toolchain
rebuilds.

1. **`mock/mock.cfg`**: update `dist`, `releasever`, `bootstrap_image`, `description`, and the
   `[fedora]`/`[fedora-updates]` repo `baseurl`s and names to the new release. Merge this on its
   own or bundled only with `fedora-repos` (see next point) and `binutils` if it's a clean
   (unmodified) package — do not bundle `glibc`/`gcc`/`llvm` here.
2. **Testing/CI fallback config** (`rpms/ci/repos/fedora-43.repo` → `fedora-44.repo`, and
   `ci/default-tests/tests-rpm.yml`'s repo copy + GPG key import): these reference the buildroot's
   Fedora version directly, and there's exactly one of each — bump them in the same MR as
   `mock.cfg` (or immediately alongside it), not later. During the F44 rebase this was deferred and
   wasn't caught until `php` failed its install test days later in Step 4, against a fallback repo
   that still pointed at the outgoing release. (This is distinct from the *per-package* test
   fixture gotcha below, which recurs throughout every step — see "Known gotchas: package-specific
   test fixtures with hardcoded Fedora-version references.")
3. **`fedora-repos`**: rebase to the new release. If Fedora's spec uses a conditional ELN-style
   macro for `Release:`, evaluate whether to keep it or hardcode a plain `Release: N%{?dist}` —
   this is a judgment call each time depending on what's simplest to maintain; document whichever
   choice is made in the package's `modification_reason`.
4. **`binutils`** (if it carries local CVE backport patches): before assuming the patches still
   apply, check whether the CVEs they address have already been fixed upstream in the new Fedora
   version. If so:
   - Pull Fedora's current spec as the new baseline (don't try to merge patch-by-patch on top of
     the old spec).
   - Re-add only the local patches that are **not** already fixed upstream.
   - For a quick validation build, it's sufficient to confirm the patch set applies and
     compilation *begins* — you don't need to wait for a full build to complete just to validate
     the patch set is sane. Kill the build once compilation starts.
5. **`glibc`**: this is the highest-risk package in this phase because of a circular bootstrap
   dependency (see "Known gotcha: glibc/gcc bootstrap circularity" below). Expect this to require a
   temporary workaround, tracked as a `modified` package, to be reverted in Step 2.
6. **`gcc`**: reapply any local modification (e.g. disabling non-essential language frontends)
   against the new spec. Expect this to be one of the longest-running builds in the whole rebase
   (multiple hours per architecture) — plan CI capacity accordingly.
7. **`llvm`**: rebase and rebuild; this can run in parallel with `gcc` and typically finishes
   faster.

## Step 2 — Bootstrap cleanup

Once the new compiler (from Step 1) is published to Pulp:

1. **Rebuild `annobin`** against the new compiler. This must happen *before* the next step, or
   reverting the glibc bootstrap workaround will immediately re-trigger the same circular
   dependency.
2. **Revert the glibc bootstrap workaround** from Step 1, now that the new compiler and annobin are
   both available. Clear the `modified` status/reason that was recording the workaround.
3. **Rebuild `libtool`** against the new compiler. This is easy to miss (it was missed during the
   F44 rebase and had to be done out-of-band mid-mass-rebuild) but several packages
   (`apr`, `audit`, `authselect`, `automake`, `avahi`, `bind`, `catatonit`, `cryptsetup`, `find`,
   and likely others) will fail to build against the new toolchain without it. **Do this here, not
   in Step 4.**

## Step 3 — Toolchain-adjacent, release-pinned packages

Rebase the remaining packages that were explicitly tracking the outgoing Fedora branch and don't
carry a permanent reason to stay behind (see "Packages that intentionally stay behind"). For each:

- Decide whether it should move to the new versioned branch (e.g. `f44`) or to `rawhide` — moving
  to `rawhide` can simplify build-dependency resolution for clean (unmodified) packages, but ties
  future updates to rawhide's churn.
- For packages with local modifications, check whether the modification is still needed (e.g. a
  security backport that may have landed upstream by now) before reapplying it blindly.
- **Check `test/rpms/<pkg>.yml` for a hardcoded Fedora-version reference** before opening this
  package's MR (see "Known gotchas: package-specific test fixtures with hardcoded Fedora-version
  references" below) — fix it in this same MR if present.

## Step 4 — Mass rebuild of everything else

Rebuild every remaining package against the new toolchain so that linking, annobin annotations, and
compiler hardening are consistent across the whole package set.

1. **Build the exclusion list first**: everything already rebuilt in Steps 1–3, plus anything
   merged in the hours immediately before starting this step (check recent merge history). Use
   `dist_git.py rebuild --all`'s `--exclude` option ([HUM-5183](https://redhat.atlassian.net/browse/HUM-5183)):

   ```bash
   ./ci/dist_git.py rebuild --all --exclude pkg1,pkg2,... --reason "<toolchain> toolchain rebuild"
   ```

   During the F44 rebase this flag didn't exist yet, so the exclusion list had to be hand-rolled
   with `./ci/dist_git.py list | grep -v -E '^(pkg1|pkg2|...)$'` piped into `rebuild` — no longer
   necessary now that `--exclude` has landed.

2. **Watch for packages whose `Release:` field doesn't fit the simple `.N` bump pattern** (e.g.
   packages using build-time macros for `Release:`) — these will fail to commit cleanly and need to
   be handled in a separate pass.
3. **Submit in batches**, not one giant MR wave — use `./ci/rebuild_multi_mr.sh --dry-run` to
   preview, then `./ci/rebuild_multi_mr.sh --max-updates=N` to submit and auto-merge-on-green in
   controlled increments. Plan for several days; a run of ~460 packages took about a week of
   elapsed time across 5 batches during the F44 rebase, gated by CI/Testing Farm throughput more
   than build time itself.
4. **Check `test/rpms/<pkg>.yml` for a hardcoded Fedora-version reference before each package's
   rebuild MR merges** (see "Known gotchas: package-specific test fixtures with hardcoded
   Fedora-version references" below) — this is what let `php`'s failure slip through undetected for
   days during the F44 rebase, so don't rely on remembering it only once at the end.
5. **Expect a small number of packages to need bespoke fixes** that a routine rebuild can't
   resolve automatically — license metadata regressions surfaced by newer scanners, compiler/linker
   regressions in vendored dependencies (e.g. a `bindgen` version incompatible with a newer clang),
   confirmed upstream compiler-triggered bugs in JIT/JVM-style software, or stale version-gates in
   local patches that assumed an older toolchain. Budget time for a handful of these near the end
   of the mass rebuild.

## Known gotchas

### glibc/gcc bootstrap circularity

A new Fedora release's `glibc` spec may use a feature of the new `gcc` (e.g. a new compiler flag),
while the new `gcc` may in turn require symbols only present in the new `glibc`. If Hummingbird's
own Pulp repos still serve the *old* toolchain at a higher priority than the new Fedora repos during
the transition window, this becomes a real chicken-and-egg problem, not just a theoretical one.

The pragmatic fix used for F44 was to drop the new-toolchain-specific flag from `glibc` temporarily
(if it's a defensive/hardening-only flag whose absence doesn't break correctness), rebuild
`glibc` and `gcc` against each other with it disabled, then re-enable it once both are published
and `annobin`/`libtool` have also been rebuilt against the new compiler (Step 2 above).

### Package-specific test fixtures with hardcoded Fedora-version references

Beyond the single global fallback config bumped in Step 1, some packages carry their own
`test/rpms/<pkg>.yml` test suite that independently hardcodes a Fedora repo/GPG-key reference for
setting up a test chroot (during F44 this was `glib2`, `hummingbird-release`, `ca-certificates`,
`crypto-policies`, and `openssl`). Don't wait for a one-time catch-all audit to find these — fix
each one in the same MR as that package's own rebuild/update, whenever it happens to land during
the rebase (Step 1, 3, or 4, whichever touches that specific package — see the reminders in each of
those steps above). Relying on an exhaustive upfront sweep of every `test/rpms/*.yml` is fragile and
is exactly what let `php`'s failure slip through undetected for days during the F44 rebase — its own
MR was fine, but a *different* package's stale GPG key/repo reference broke shared test
infrastructure `php` depended on. When you open the rebuild/update MR for any package, grep
`test/rpms/<pkg>.yml` for the outgoing release string first.

See [HUM-5184](https://redhat.atlassian.net/browse/HUM-5184) for tracking a structural fix (single
source of truth or a CI lint check) so this class of drift is caught automatically instead of
relying on either of the above being remembered.

### Automation racing a manual mass rebuild

The routine automated dist-git sync bot keeps running on its normal schedule regardless of an
in-progress manual mass rebuild. If both touch the same packages in the same window, you'll get a
wave of merge-conflicted bot MRs that need manual cleanup — during F44 this was roughly 70 MRs at
once. Decide before starting Step 4 (see "Pre-flight checks" above) whether to pause routine sync
automation for the duration of the mass rebuild, or accept the cleanup cost.

### External CI capacity constraints

Large rebuild waves can coincide with unrelated capacity crunches on shared infrastructure (e.g.
Testing Farm). These are outside Hummingbird's control but can stall a batch for a day; check
external status pages when a batch is unexpectedly slow (for reference, the F44 rebase hit
<https://status.testing-farm.io/issues/2026-07-14-big-queue-in-red-hat-ranch/>).

### python3dist / rawhide-tracked toolchain-adjacent packages

If any packages central to a specific language ecosystem's "what version is default" (e.g.
`python-rpm-macros`) are tracking `rawhide` rather than a pinned branch, a routine automated sync
during this window can silently pull in an experimental version bump (e.g. Fedora bootstrapping the
*next* language version) that corrupts auto-generated package metadata across dozens of downstream
packages without any build failing immediately. See HUM-4988 and HUM-4989 for the full incident
report and proposed guardrails from the F44 rebase. Decide during "Pre-flight checks" above whether
any "default version" declaration packages need the same branch-pinning treatment as the
interpreter itself before you start.

## Packages that intentionally stay behind

Not every package needs to move to the new branch. Document (in `modification_reason` and/or the
epic tracking the rebase) any package that's staying pinned to the old branch, and why — otherwise
this looks like unfinished work to the next person. Reasons seen so far:

- **FIPS validation**: the package carries a modification tied to FIPS certification, and moving to
  newer upstream content risks invalidating that certification. The package is still rebuilt
  against the new toolchain — it just doesn't pick up newer Fedora source content.
- **Package no longer exists upstream**: Fedora sometimes drops a package entirely in a new release
  (e.g. an older LTS language runtime superseded by a newer one). If Hummingbird still needs to
  carry it, it has no newer branch to rebase to and simply stays on its last available branch,
  rebuilt against the new toolchain.

## Reference: commands used during the F44 rebase

These are shown verbatim from the F44 rebase for concreteness. When reusing them for the next
rebase (e.g. F45), remember to substitute the actual target release everywhere `F44` appears below
— including the exclusion list of already-rebuilt packages, which will differ each time.

```bash
# Preview a rebuild plan across all packages
./ci/rebuild_multi_mr.sh --dry-run

# Submit rebuild MRs in controlled batches, auto-merge on green
./ci/rebuild_multi_mr.sh --max-updates=50

# Rebuild everything except a known set of already-handled packages, using the
# --exclude flag (HUM-5183, landed after the F44 rebase — see Step 4 above)
./ci/dist_git.py rebuild --all --exclude glibc,gcc,llvm,annobin,binutils,fedora-repos,python3.13,python-gitlab --reason "F44 toolchain rebuild"
```

During the F44 rebase itself, `--exclude` didn't exist yet, so the equivalent exclusion list had to
be hand-rolled:

```bash
./ci/dist_git.py rebuild $(./ci/dist_git.py list | grep -v -E '^(glibc|gcc|llvm|annobin|binutils|fedora-repos|python3.13|python-gitlab)$') --reason "F44 toolchain rebuild"
```

## Related tickets

- [HUM-2018](https://redhat.atlassian.net/browse/HUM-2018) — F44 buildroot rebase epic (full
  history)
- [HUM-3369](https://redhat.atlassian.net/browse/HUM-3369) /
  [HUM-3370](https://redhat.atlassian.net/browse/HUM-3370) /
  [HUM-4536](https://redhat.atlassian.net/browse/HUM-4536) /
  [HUM-4537](https://redhat.atlassian.net/browse/HUM-4537) — Steps 1-4, each with a detailed
  what/why/fix writeup
- [HUM-4988](https://redhat.atlassian.net/browse/HUM-4988) /
  [HUM-4989](https://redhat.atlassian.net/browse/HUM-4989) — python-rpm-macros/python3dist
  regression incident and guardrails
- [HUM-5182](https://redhat.atlassian.net/browse/HUM-5182) — `bump_release()` double-suffix bug
- [HUM-5183](https://redhat.atlassian.net/browse/HUM-5183) — `dist_git.py rebuild --exclude`
  option (implemented)
- [HUM-5184](https://redhat.atlassian.net/browse/HUM-5184) — generalize the testing-infra
  Fedora-version drift fix
