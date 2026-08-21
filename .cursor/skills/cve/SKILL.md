---
name: cve
description: >-
  Investigate and resolve individual HUM CVE tracker tickets. Covers
  showing ticket details, determining if Hummingbird is affected,
  backporting patches, setting Fixed in Build, and closing misfiled
  tickets. Use when the user says /cve, "let's look at HUM-XXXX"
  (a CVE tracker), or asks to investigate/fix a specific CVE.
  "/cve HUM-XXXX only" skips same-CVE sibling discovery.
---

# CVE Tracker Investigation

Investigate individual HUM CVE tracker tickets, determine whether
Hummingbird is affected, and take the appropriate resolution action.

This skill is for hands-on, per-ticket work. For bulk triage of
`cve-needs-attention` tickets, use `/analyse-cve-needs-attention`.
For a high-level overview, use `/cve-status`.

## Procedure

### Preparation: Run Jira commands reliably (HUM-4821)

Use `cve_helper.py` for Jira reads and writes. Ticket show/bot-MR/SBOM
still use `rhjira`/`glab` under the helper. Writes import
`hummingbird_cve_analysis.lib.jira_client` (HUM-6002). Do not invent
ad-hoc Python subprocess wrappers, background polling workers, or
long-running retry loops in the agent session.

`cve_helper.py deps` checks that Jira auth and the analysis package
import. Auth is the existing rhjira file: `JIRA_TOKEN` plus `JIRA_SERVER`
mapped to `JIRA_URL`, and `JIRA_EMAIL` as Basic-auth user. Source
`~/.config/rhjira/agent.env` is loaded automatically when `JIRA_TOKEN`
is unset. Do not create a second Jira token.

Install the analysis package, or point at a tools checkout:

```bash
export HUMMINGBIRD_TOOLS_ROOT=/path/to/hummingbird/tools
python .cursor/skills/cve/cve_helper.py deps
```

`cve_helper.py` already retries transient Jira/proxy failures for its
own `rhjira` calls. Prefer the helper subcommands below for repeated
probes so the agent does not re-paste shell boilerplate.

`/cve` workflows require outbound internet access for tools like
`rhjira`, `glab`, and SBOM/Pulp endpoints. Running these commands in a
sandboxed/no-network context can produce false failures
(auth/proxy/timeout/connection errors) and block ticket automation.
Use an internet-enabled execution context for these tool calls.

For **direct** `rhjira` writes still done in the shell (comment, edit,
status):

1. Prefer `python .cursor/skills/cve/cve_helper.py …` for ticket show,
   bot-MR, SBOM, and spec probes.
2. Always pass `--noeditor` on writes.
3. Do not paste a retry function into the session. `cve_helper.py`
   already retries transient Jira/proxy failures for reads (ticket show,
   bot-MR, SBOM, spec probes). For raw `rhjira` writes, run once with
   `--noeditor`; if it fails for a non-auth reason, report it and stop.
   No polling loops (`while ... sleep 30`).
4. On `login failure` / invalid token, source credentials once and
   retry a single time:

   ```bash
   set -a
   source ~/.config/rhjira/agent.env
   set +a
   ```

   If it still fails, stop and tell the user. Do not debug rhjira
   further in the session.
5. Report per-ticket Jira status clearly before stopping, for
   example:

   ```text
   HUM-1234: jira_unavailable (proxy tunnel 403; no changes applied)
   HUM-1235: read_ok
   ```

### Step 0: Check for in-flight bot updates

Before manual remediation, check whether the automation bot already has
an update MR:

```bash
python .cursor/skills/cve/cve_helper.py bot-mrs <package>
# optional: --json  --per-page 5
```

**Evaluating a bot MR:**

1. Check the version being updated against the CVE fix version
2. Review the MR diff or changelog to confirm the fix is included
3. The bot MR may not reference the CVE ID explicitly but still fix it

If a bot MR exists and includes the CVE fix:

- **Open MR:** Link it to the CVE tracker and monitor it instead of
  creating duplicate work
- **Merged MR:** The CVE is already fixed; proceed to Step 3a to set
  Fixed in Build

Only proceed with manual remediation (Step 2+) if no bot MR exists or
the bot MR's version doesn't include the CVE fix.

### Step 1: Show the ticket(s)

If the user provides bare numbers (e.g. "2875" or "/cve 2875"),
treat them as HUM tickets by prepending `HUM-`.

**`only` keyword:** If the user writes `only` with the ticket list
(e.g. `/cve HUM-1234 only` or `/cve HUM-1234 HUM-1235 only`), pass
`--no-find-related` and restrict analysis and writes to those keys.
Do not recap same-CVE siblings.

```bash
python .cursor/skills/cve/cve_helper.py HUM-XXXX
# equivalent: python .cursor/skills/cve/cve_helper.py show HUM-XXXX
```

For multiple tickets, pass them in one call:

```bash
python .cursor/skills/cve/cve_helper.py HUM-1234 1235 1236 \
  --json-out /tmp/cve-triage.json
```

With `only`:

```bash
python .cursor/skills/cve/cve_helper.py HUM-1234 --no-find-related
```

After the ticket summary, run the deterministic probes for the
package(s) under investigation (Step 0 bot-mrs, plus Step 2a/2e as
needed) before doing ad-hoc shell greps.

**Finding Related Tickets:** `cve_helper.py` automatically discovers all open
HUM tickets with the same CVE IDs — this is on by default (unless the
user said `only`) so you get the full set in one invocation:

```bash
# Default: shows HUM-1234 + any open tickets sharing its CVEs
python .cursor/skills/cve/cve_helper.py HUM-1234

# User said "only": show only HUM-1234
python .cursor/skills/cve/cve_helper.py HUM-1234 --no-find-related
```

Related tickets are useful context for versioned packages (e.g.
ruby3.3/ruby4.0, llvm/llvm21). **User-named tickets are in-scope for
Step 3. Discovered tickets are context only until the user says
otherwise.** After Step 2h, if extras exist, ask:

```text
Related: HUM-A (pkgA), HUM-B (pkgB). Apply this to all, or only the
tickets you named?
```

A "Yes please" on the named set is **not** approval for discovered
siblings. The related tickets are fetched during the initial show so
there is no need for a second invocation.

Use the script output as the primary source for:

- CVE IDs and package guess (extracted via 3-tier fallback: summary
  `CVE-XXXX pkgname:` pattern → `pscomponent:` label → Fixed in Build
  name prefix)
- Status, severity, labels, fixed-in-build
- Linked HUM tickets and linked task MR URLs
- Flaw description (vulnerability details from the ticket Description)
- Upstream Affected Component
- cve_analysis `{noformat}` block (full bot assessment)
- `suggested_chat_title` (deterministic title for `rename_chat`)

**Discovered vs initial tickets:** User-provided tickets get full detail
(severity, FIB, linked tickets, comments, cve_analysis). Discovered
related tickets are batch-fetched with lightweight fields only (summary,
status, type, assignee, labels) — severity, FIB, linked tickets, and
cve_analysis are absent. When you need full detail on a discovered
ticket (e.g., to set FIB or check its cve_analysis), run
`python .cursor/skills/cve/cve_helper.py HUM-XXXX --no-find-related`
or `rhjira show` on that specific ticket.

Also inspect comments for: Hummingbird SRPM version, CVE affected range,
upstream fix info (commits, PRs), and blocking Task tickets with MRs (work
already in progress). Review all comments for prior analysis, decisions, or
FIB values already set. Note any `*.sbom.json` attachments listed by
`rhjira show` — `cve_analysis` attaches `{nvr}.sbom.json` for SBOM-backed
reviews; reuse a matching NVR in Step 2e instead of re-fetching from Pulp.
Summarize relevant context — if it's already been resolved, say so rather
than re-doing the work.

**EMBARGO CHECK:** If the ticket summary starts with `EMBARGOED`,
stop immediately and output:

```text
WARNING: HUM-XXXX is EMBARGOED. Embargoed CVEs must not be
discussed, analyzed, or acted upon in this tool. Stopping.
```

Do not proceed with any analysis, comments, or code changes.

**Batch Resolution:** When multiple tickets share a CVE (e.g., ruby3.3/ruby4.0,
llvm/llvm21), investigate once but **verify each package individually**:

1. **Initial triage:** Use helper output to identify which packages likely have
   the component (product-mismatch warnings, vendored deps indicators).
2. **Per-package verification:** For each package, verify using Step 2 methods
   (spec file bundled deps, SBOM, code inspection). Do not assume uniformity.
3. **Group by outcome:** Collect tickets by resolution (NAB/component-absent,
   FIB, needs-update).
4. **Batch actions by outcome** (only after the user confirms
   scope — named tickets vs all related):
   - **Same resolution:** Write one comment template, post to each
     in-scope ticket in a loop, create one task linking those
     trackers, set FIB on each if applicable.
   - **Mixed outcomes:** Handle each group independently. Close
     unaffected ones directly, create tasks for those needing fixes.

> IMPORTANT: If a patch is required, handle each ticket separately.

Use the helper output as the primary source. Only fall back to
`rhjira show`/`rhjira dump` when the helper output is not sufficient
to resolve the ticket (e.g., you need raw comment text or fields the
helper does not extract).

After showing the ticket(s), rename the chat using `suggested_chat_title`
from `cve_helper.py`. Use `--title-prefix` for resolution-specific labels
(e.g. `"FIB"`, `"NAB"`, `"!${MR_IID}"`):

```bash
python .cursor/skills/cve/cve_helper.py HUM-XXXX --title-only --title-prefix "FIB"
```

### Step 2: Determine if Hummingbird is affected

The user will typically ask to investigate. Use whichever of these
approaches fits the situation:

**Web search efficiency:** Check local sources first—the
`cve_helper.py` output (flaw description, upstream component,
cve_analysis block), local code, SBOM, metadata. The flaw
description usually names the specific vulnerable sub-component
and often resolves mismatch cases without any web search.

**Fix verification priority:**

  1. cve_helper.py probes + local source (see 2a–2d),
  2. download upstream tarball and grep WHATS_NEW/CHANGELOG,
  3. curl Fedora spec for patches,
  4. web search for component relationships only.

For same-day CVEs, skip web searches for fix info, check changelog directly.
If no fix mention and no upstream fix commit is known, proceed to Step 3f.
If a fix commit exists but isn't in the changelog, continue with Step 2f to
verify the commit date against the SRPM release tag.

When you do search:

- **Product mismatch** (CVE vendor/product differs from Hummingbird package):
  **First: Extract the vulnerable sub-component** from the CVE description/flaw
  field before concluding mismatch. The CVE often targets a specific module,
  plugin, or feature — check whether *that* exists in the Hummingbird package,
  not just whether the product names match. Many mismatches resolve once the
  component is identified.

  1. **Search official documentation** for feature/component support
     Example: `"<HummingbirdPkg>" "<CVE_component>" support documentation`
  2. **For forks**: Go directly to compatibility/feature-difference documentation
     Example: `"<fork>" "<upstream>" compatibility differences documentation`
  3. **Then: Search architectural differences** if docs unclear
     Example: `"<packageA> vs <packageB>" <component> differences`
  4. **Prioritize:** Official project docs > vendor comparisons > blog posts
  
- **Component presence**: Search whether the package implements the vulnerable
  component, not the CVE ID itself
  
- **Avoid CVE ID searches**: Recent CVEs are often not yet indexed in
  NVD/CVE.org; search product/component relationships instead
  
- **Avoid over-specific quoted searches**: If a quoted search returns no results,
  immediately fall back to fewer quotes/broader terms rather than trying variations

#### 2a: Spec file bundled dependency check

For product-mismatch CVEs, check the spec file for bundled dependencies
before doing anything else — including version comparison:

```bash
python .cursor/skills/cve/cve_helper.py spec-deps <package> \
  --component "<component-name>"
# optional: --json
```

This reports `Provides: bundled(...)` lines and other component
mentions with line numbers. If the component is not found here, the
SBOM match is a false positive — treat as not present and close as NAB
without proceeding to version comparison or SBOM verification.

If found, compare against the CVE affected range and continue to Step 2c.

#### 2b: Vendored dependency check (Go packages)

The `cve_helper.py` output already shows `Vendored deps: yes (go-vendor-tools)`
for any package with a `go-vendor-tools.toml` — use this as a first check.

For module-level presence, use SBOM verification (Step 2e) which identifies
the specific vendored module, its version, and whether it's runtime-installed.

If not vendored by any package, the ticket is misfiled.

#### 2c: Version comparison

Compare the Hummingbird SRPM version against the CVE affected range.
If the SRPM version is above the fix version, the package *may* be
fixed -- but see the critical rule in Step 2e before concluding.

For product-mismatch CVEs, only reach this step after Step 2a confirms
the component is actually present.

#### 2d: Code inspection

Check the Hummingbird package source for the vulnerable code:

```bash
ls rpms/<package>/
```

Read the spec file and any existing patches. If patches addressing
the CVE are already applied, the package is fixed.

**For bundled dependencies:** Extract from the CVE description the specific
vulnerable code configuration or pattern (e.g., specific function calls,
configuration options, or preconditions). Verify it is NOT used:

1. Search the package source comprehensively:

   ```bash
   grep -r "<vulnerable_pattern>" rpms/<package>/ --include="*.ts" --include="*.js" --include="*.py" --include="*.go"
   grep -r "<vulnerable_pattern>" rpms/<package>/ --include="*.md" --include="*.txt"
   ```

2. Check test files, examples, and documentation:

   ```bash
   find rpms/<package>/ -type f \( -name "*test*" -o -name "*example*" -o -name "*doc*" \) \
     -exec grep -l "<vulnerable_pattern>" {} \;
   ```

3. If the package imports or vendors the component, download the upstream source
   and verify the pattern is not invoked there either.

4. If still unclear, web search the package name + vulnerable pattern to confirm
   the feature is not supported.

If the vulnerable pattern is NOT found across all sources and references,
the code path is unreachable and you can close as NAB "Vulnerable Code not Present"
without version checks.

#### 2e: SBOM verification

When the CVE product differs from the Hummingbird package name,
obtain and inspect the SBOM to determine:

- whether the CVE product/component is present at all
- whether it is runtime-installed in shipped binary RPMs
- or only a build-time/test-time dependency

**Prefer a Jira-attached SBOM when it matches the NVR you need.**
`cve_analysis` attaches the analyzed package SBOM to the tracker as
`{nvr}.sbom.json` (for example
`grafana13.1-13.1.1-0.5.hum1.sbom.json`). Reuse that copy instead of
re-fetching from Pulp when the attachment NVR matches the version
under investigation.

1. Determine the target NVR (prefer the most specific source
   available):
   - Latest `cve_analysis` comment `NVR:` line
   - `Hummingbird repo (latest): … / {nvr}.src.rpm` (strip
     `.src.rpm`)
   - Fixed in Build (strip `.src.rpm`) when that is the SRPM being
     verified
2. Check the ticket Attachments list from `rhjira show` (or the
   Attachments section already visible after Step 1) for
   `{nvr}.sbom.json`.
3. Fetch and search with the helper (Jira attachment first when
   `--ticket` and `--nvr` are set, else Pulp):

   ```bash
   python .cursor/skills/cve/cve_helper.py sbom <package> \
     --ticket HUM-XXXX \
     --nvr <nvr> \
     --search "<cve-product>" \
     --search "<module>" \
     --out /tmp/<package>.sbom.json \
     --json
   ```

   Include `--json` in the first call to get structured output. If runtime
   vs build-time determination requires deeper SBOM inspection, consolidate
   jq queries instead of making multiple separate calls.

When multiple `*.sbom.json` attachments exist, use the one whose
NVR matches the target from step 1 — do not assume the newest
attachment filename is correct without that check. If the attached
NVR does not match the version being investigated, omit `--nvr` /
`--ticket` so the helper falls back to Pulp.

The helper prints structured hits (name/version/purl/scope/type)
when present. Use those fields — plus any follow-up inspection of
`metadata.component` / package relationships — to decide whether the
component is runtime-installed vs build/test-only.

If the component's role is unclear from SBOM metadata, search to clarify its purpose.

Decision guidance:

- If component is present in runtime binary package contents,
  treat as potentially affected.
- If component appears only in build/test toolchain paths and is
  not present in installed runtime binary RPM contents, treat as
  not runtime-affected.
- If the binary check is unknown (`mismatch_binary_unknown`) or SBOM
  evidence is ambiguous, cross-check with the spec file (Step 2a)
  before concluding. If spec-deps shows no bundled reference to the
  component, the SBOM match is a false positive — treat as not present.

For any `Not a Bug` recommendation based on product mismatch,
include SBOM evidence in the Jira comment:

- SBOM source used (Jira attachment `{nvr}.sbom.json` and/or Pulp
  URL/file)
- exact match/no-match terms
- runtime-installed vs build-time-only conclusion
- why that supports the chosen VEX justification

#### 2f: Upstream fix verification

**CRITICAL:** Never conclude "fixed" based solely on the Hummingbird
SRPM version being numerically higher than a distro's patched
version. The fix may have been committed to upstream *after* the
Hummingbird SRPM's release tag.

When recommending Done-Errata:

1. Identify the upstream fix commit
2. Compare its date against the SRPM's upstream release tag date
3. Only confirm fixed if the fix commit predates the release tag

#### 2g: Known version-scheme issues

**dotnet packages:** SRPM uses SDK versions (e.g. 9.0.116), CVEs
use runtime versions (e.g. 9.0.16). Mapping: SDK third component
mod 100 = runtime patch. See `/analyse-cve-needs-attention` for
full details.

**Go pseudo-versions:** e.g. `v0.0.0-20260507153023-abc123` means
a commit snapshot, not a proper release tag. Compare the commit
date and hash against the fix, not the version number.

#### 2h: Resolution Summary

After completing your Step 2 investigation, **state your recommended
resolution to the user**. Lead the recap with:

- Assignee (the user vs the Jira bot)
- Labels
- Fixed in Build (set or unset)
- Which resolution path applies (e.g., "3a: Already fixed — set FIB",
  "3b: Not affected — close as NAB", "3d: Needs version bump",
  "3f: Affected, no upstream fix — cve-next-release")
- Why (brief justification based on your investigation)
- What evidence supports it (version comparison, SBOM findings, code
  inspection results, upstream fix verification)

If FIB is already set and a task/MR already exists, recommend leaving
the ticket for advisory automation. Do not re-investigate from scratch
or set FIB again unless the user asks.

If FIB is set but no task/MR exists yet, flag this to the user and ask
whether to create the task before proceeding.

If related tickets were discovered (and the user did not say `only`),
list them and ask whether to apply the same resolution to all or only
the named tickets.

Then wait for the user's direction before proceeding with Step 3 actions.

### Step 3: Take action

Based on the analysis, one of these paths applies:

<!-- markdownlint-disable MD029 -->

#### 3a: Already fixed -- set Fixed in Build

When the fix is confirmed present in the shipped SRPM:

1. Add a comment documenting the evidence (patches applied,
   version comparison, upstream commit dates)
2. Set Fixed in Build:

   ```bash
   python .cursor/skills/cve/cve_helper.py comment HUM-XXXX \
     -f /tmp/cve-comment.txt
   python .cursor/skills/cve/cve_helper.py set-fib HUM-XXXX \
     <name>-<version>-<release>.src.rpm --package <package>
   ```

3. Rename the chat (see Step 1) with `--title-prefix "FIB"`.
4. **Do NOT close the ticket and do NOT open a manual advisory MR.**
   Set Fixed in Build and leave the ticket open. The cve_analysis
   automation opens the advisory MR and closes the ticket as
   Done-Errata. The manual advisory steps in
   `cve-manual-process.md` Step 8 are a human fallback when
   bypassing automation — do not follow them during `/cve` unless
   the user explicitly asks or automation is blocked (e.g.
   `advisory-mr-failed`).

#### 3b: Not affected / misfiled -- close as Not a Bug

When the CVE does not apply (wrong product, component not present,
disputed):

1. For product-mismatch or component-absence cases, perform SBOM
   verification first (Step 2e) and capture runtime-vs-build-time
   evidence for binary RPM installation status.
2. **Name the specific component** extracted from the CVE description
   in the closing comment. State why it was checked and why it is
   absent from the Hummingbird package.
3. Write a closing comment to a temp file explaining why
4. Post the comment:

   ```bash
   python .cursor/skills/cve/cve_helper.py comment HUM-XXXX \
     -f /tmp/close-comment.txt
   ```

5. **Ask the user for approval before closing.** Then use the
   appropriate VEX justification:

   ```bash
   python .cursor/skills/cve/cve_helper.py close-nab HUM-XXXX \
     --vex "Component not Present" -f /tmp/close-comment.txt

   python .cursor/skills/cve/cve_helper.py close-nab HUM-XXXX \
     --vex "Vulnerable Code not Present" -f /tmp/close-comment.txt
   ```

6. Rename the chat (see Step 1) with `--title-prefix "NAB"`.
7. Verify: `rhjira show HUM-XXXX 2>&1 | grep "^Status:"`

If SBOM retrieval is unavailable (network/policy/tooling), do not
close as `Component not Present` yet. Document the blocker in a
comment and ask the user whether to proceed with a manual override.

#### 3c: Duplicate (unversioned package)

When a ticket is filed against an unversioned base name (e.g.
`ruby`) but versioned SRPMs exist (e.g. `ruby3.3`, `ruby4.0`):

1. Search for versioned tickets:

   ```bash
   rhjira list "project = HUM and summary ~ CVE-YYYY-NNNNN" \
     --rawoutput --numentries 10 --fields key,summary,status
   ```

2. If versioned tickets exist, recommend closing as Duplicate

#### 3d: Needs version bump (preferred)

**Prefer a version bump over a backport** in almost all cases.
Updating to a newer upstream release is cleaner, picks up
additional fixes, and avoids carrying local patches. Only fall
back to a backport (Step 3e) for packages where a version bump
is too risky (e.g. glibc, binutils).

When the package is affected and a newer upstream release
contains the fix:

1. **Create a HUM task ticket** and link it to the CVE tracker(s):

   ```bash
   rhjira create --noeditor --project HUM --tickettype Task \
     --summary "<package>: Update to <version> for <CVE-ID>" \
     --assignee <user>@redhat.com
   ```

   Then link and activate (one `--blocks` per edit call):

   ```bash
   rhjira edit HUM-YYYY --noeditor --blocks HUM-XXXX
   rhjira edit HUM-YYYY --noeditor --status "In Progress"
   ```

   Either `--blocks` or `--isblockedby` works for linking;
   both patterns appear in practice.

2. **Create a worktree** for the task. This keeps each CVE
   fix isolated so multiple can be in flight at once:

   ```bash
   python .cursor/skills/cve/cve_helper.py worktree HUM-YYYY
   # prints WORKTREE=/…/worktrees/HUM-YYYY
   ```

   Then move the agent workspace into the worktree:

   ```text
   CallMcpTool: cursor-app-control / move_agent_to_root
     rootPath: <absolute path to worktree, from command above>
   ```

   All subsequent file operations (spec edits, tarball downloads,
   metadata updates when required, `build_rpms.sh`) happen inside
   the worktree.

3. **Download and verify the new tarball** -- download the
   tarball and signature, then verify the GPG signature. If
   the signing key changed between releases, update `Source2:`
   in the spec and `git rm` the old key file.

4. **Check existing patches** -- for each `PatchN:` in the
   spec, try `patch --dry-run -p1` against the extracted new
   source. Drop patches that were upstreamed: remove the
   `PatchN:` line from the spec and `git rm` the patch file.

5. **Update the spec file:**
   - `Version:` to the new version
   - `Release:` -- use `0.1%{?dist}` when ahead of Fedora
     (sorts below Fedora's eventual `1%{?dist}`). If Fedora
     already has this version, use the `.1` suffix on their
     release number instead.
   - Update `Source2:` if the GPG signing key changed

6. **Update supporting files:**
   - `sources` -- SHA512 checksums for the new tarball and
     signature (see `documentation/operating/lookaside-cache-access.md`)
   - `.gitignore` -- update the version glob pattern if the
     new version falls outside the existing range
   - `metadata/<package>.json` -- update `version` to the new
     version. Metadata `release` is the base release for
     `dist_git.py` update/rebuild bookkeeping (no dist tag),
     **not** a CVE/FIB field. When ahead of Fedora, set it to
     the local base (typically `0.1`, matching spec
     `0.1%{?dist}`). When Fedora already has this version, set
     it to the Fedora/rawhide baseline. See
     `documentation/operating/package-metadata-fields.md`.

7. **Lookaside and build pipeline setup (when ahead of
   Fedora):** When Fedora has not yet released this version,
   the tarball must be served from the Hummingbird lookaside
   cache instead of Fedora's. Three things are required:
   - Copy the tarball (and signature if listed in `sources`) to
     `/tmp` and print the upload commands using the **RPM package
     name** (e.g. `grafana13.1`, not `grafana13`). Do **not** run
     the upload unless the user asks:

     ```bash
     cp <tarball> /tmp/
     # Give the user these commands; wait unless they ask you to run them:
     ./ci/upload-to-lookaside-cache.sh -f /tmp/<tarball> -p <package>
     ./ci/upload-to-lookaside-cache.sh -f /tmp/<sig-file> -p <package>
     ```

   - Add a `forked_from` entry for the package in
     `ci/package-overrides.yaml` (alphabetical order):

     ```yaml
     <package>:
       forked_from: "https://gitlab.com/redhat/hummingbird/rpms"
     ```

     This tells both local `build_rpms.sh` and the Konflux
     pipeline to download sources from the Hummingbird
     lookaside instead of Fedora's.
   - Run `make generate` to regenerate the `.tekton/` YAML
     files with the new `forked-from` pipeline parameter
     (see `documentation/operating/adding-independent-packages.md`
     step 5).
   - Include `.tekton/` and `package-overrides.yaml` in the
     commit.

8. **Update package metadata status only when required.** Follow
   `documentation/operating/package-metadata-fields.md` (authoritative
   for `modification_status`, `modification_reason`, and metadata
   `release`). Read `metadata/<package>.json` first:

   - **`independent`:** Do **not** run `mark-modified`. Leave
     `modification_status` as `independent` and do **not** add
     `modification_reason`. Independent packages have no Fedora
     auto-update to block; the CVE fix is tracked in the
     spec/patch and git history.
   - **`clean` or `modified` (Fedora-imported):** Mark modified so
     auto-updates stay blocked. Set `modification_reason` to a
     short explanation that includes the CVE ID (see
     `package-metadata-fields.md`). If a reason already exists,
     **append** the new CVE (semicolon-separated) rather than
     replacing it.

   ```bash
   # Fedora-imported only — first CVE fix for this package:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "Backport CVE-YYYY-NNNNN"

   # Fedora-imported only — already modified; append the CVE:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; CVE-YYYY-NNNNN"
   ```

9. **Commit, validate, build, push, MR.** Commit with a
   descriptive message, then:

   ```bash
   make check
   ```

   ```bash
   ./ci/build_rpms.sh <package>
   ```

   Push the branch and create an MR. The MR description
   must include:
   - A 1-3 sentence summary of what was changed and why
   - `Closes: HUM-YYYY` (the task ticket -- **never** the CVE
     tracker ticket)
   - `Ref: HUM-XXXX, HUM-ZZZZ` (comma-separated CVE tracker
     ticket keys)
   - `CVE: CVE-YYYY-NNNNN, CVE-YYYY-MMMMM` (comma-separated
     CVE IDs)

   Use `-F` with a file because `-m` does not support multiple
   paragraphs:

   Detect the user's fork remote and derive the GitLab project
   path for `--head` (do not hardcode `origin` or a username):

   ```bash
   FORK_REMOTE=$(git remote -v | grep '(push)' | grep -v "redhat/hummingbird/rpms" | head -1 | awk '{print $1}')
   if [ -z "$FORK_REMOTE" ]; then
     echo "ERROR: Could not detect fork remote. Check 'git remote -v'." >&2
     exit 1
   fi
   FORK_URL=$(git remote get-url "$FORK_REMOTE")
   FORK_PROJECT=$(echo "$FORK_URL" | sed 's|.*gitlab\.com[:/]||; s|\.git$||; s|/$||')
   git push -u "$FORK_REMOTE" HUM-YYYY
   ```

   Then create the MR using `glab`:

   ```bash
   cat > /tmp/mr-description.txt << 'EOF'
   HUM-YYYY: <package>: <short title>

   <1-3 sentence description of the change: what was backported
   or updated, where the fix came from, and why>

   Closes: HUM-YYYY
   Ref: HUM-XXXX, HUM-ZZZZ
   CVE: CVE-YYYY-NNNNN, CVE-YYYY-MMMMM
   EOF
   MR_URL=$(glab mr create \
     --source-branch HUM-YYYY \
     --target-branch main \
     --head "$FORK_PROJECT" \
     --repo redhat/hummingbird/rpms \
     --title "HUM-YYYY: <package>: <short title>" \
     --description "$(tail -n +3 /tmp/mr-description.txt)")
   MR_IID=$(echo "$MR_URL" | tail -1 | grep -oE '[0-9]+$')
   ```

  After creating the MR, rename the chat title to include the MR ID:

  ```bash
  MR_TITLE=$(python .cursor/skills/cve/cve_helper.py HUM-XXXX --title-only --title-prefix "!${MR_IID}")
  ```

  ```text
  CallMcpTool: cursor-app-control / rename_chat
    title: "<value from MR_TITLE, e.g. !1234 HUM-6789 foo>"
  ```

   Add the MR link as a comment on the HUM task ticket so
   future lookups can see the work is already in review:

   ```bash
   rhjira comment HUM-YYYY --noeditor \
     -m "MR: https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests/${MR_IID}"
   ```

   After the MR pipeline has started, trigger the automated code
   review. Check the pipeline status first:

   ```bash
   glab ci status --branch HUM-YYYY --repo redhat/hummingbird/rpms
   ```

   Once the pipeline is running, trigger the review:

   ```bash
   glab mr note "$MR_IID" --repo redhat/hummingbird/rpms -m "/hummingbird code-review"
   ```

10. **Close the task ticket.** After the MR is created (do not
   wait for it to merge), transition the HUM task ticket to
   Closed:

   ```bash
   jira issue move HUM-YYYY "Closed"
   ```

If multiple CVE trackers are fixed by the same version bump,
create one task and link all CVE trackers to it. Close any
duplicate tasks as "Won't Do".

#### 3e: Needs backport (fallback)

Use a backport only when a version bump is too risky -- for
example, glibc or binutils where a full version change could
break ABI compatibility or introduce regressions.

When the package is affected and an upstream fix exists but a
version bump is not appropriate:

1. **Create a HUM task ticket and worktree** (same as 3d
   steps 1-2).

2. **Backport the patch** following the process in
   `documentation/operating/rebuilding-packages.md`:
   - Download the patch from upstream (`curl`, `git format-patch`,
     or `git diff tag1..tag2`)
   - Add `PatchN:` to the spec file
   - **Check for a gorget source-pipeline** (see
     `documentation/operating/rebuilding-packages.md` step 3 —
     "Check for a gorget source-pipeline"): run
     `grep -n -E "patch -p[0-9]|git apply" metadata/<package>.source-pipeline.yaml`
     (not exhaustive — read the `transform:` step directly if this
     misses something). If it exists and its `transform:` step
     applies patches or
     resolves a lockfile (`yarn install`, `pnpm fetch`, `npm ci`,
     `go mod vendor`), and your new patch touches a file that step
     depends on (`yarn.lock`, `package.json`, `pnpm-lock.yaml`,
     `go.mod`, `go.sum`, `Cargo.lock`), add the same
     `patch -p1 < "${PACKAGE_DIR}/<your-patch>.patch"` line there
     too, in the same relative order as the `PatchN:` slot. This is
     not optional — skipping it silently lets the generated cache/vendor
     archive drift from what `%build` actually applies. See "Known
     sharp edge: patch-list duplication" in
     `documentation/design/source-pipeline-tool.md` for the
     grafana12.4/grafana13.1/trivy incidents that motivated this
     rule.
   - **Bump the spec `Release:` field correctly.** The metadata `release`
     field is the current base release (Fedora/rawhide baseline,
     or local `0.1` if already ahead of Fedora) — use that as the
     base. Append or increment a `.N` micro-bump suffix:
     (see `documentation/operating/package-metadata-fields.md`):

     | Metadata release | Current spec Release | New spec Release |
     | --- | --- | --- |
     | `1` | `1%{?dist}` | `1.1%{?dist}` |
     | `1` | `1.1%{?dist}` | `1.2%{?dist}` |
     | `1` | `1.2%{?dist}` | `1.3%{?dist}` |
     | `0.1` | `0.1%{?dist}` | `0.1.1%{?dist}` |
     | `5` | `5%{?dist}` | `5.1%{?dist}` |
     | `5` | `5.2%{?dist}` | `5.3%{?dist}` |

     The base part **must** match the metadata `release` value.
     Never invent a new base (e.g. `1` to `2`, or `0.1` to `1`).
     Only the micro-bump after the base changes. If the spec
     already has a micro-bump, increment it; if not, add `.1`.

   - If the spec uses `%patch N -p1`, use that form; if
     `%autosetup -p1`, patches apply automatically

3. **Update package metadata status only when required.** Same
   rules as the version-bump path step 8 — follow
   `documentation/operating/package-metadata-fields.md`. Read
   `metadata/<package>.json` first:

   - **`independent`:** Do **not** run `mark-modified`. Keep
     `modification_status: "independent"` and do **not** add
     `modification_reason`.
   - **`clean` or `modified` (Fedora-imported):** Mark modified
     with a short explanation that includes the CVE ID (append
     if a reason already exists):

   ```bash
   # Fedora-imported only — first CVE fix for this package:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "Backport CVE-YYYY-NNNNN"

   # Fedora-imported only — already modified; append the CVE:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; CVE-YYYY-NNNNN"
   ```

   **Do NOT change the metadata `release` field for backports.**
   Keep it at the current base (Fedora/rawhide or local
   placeholder). If `mark-modified` changes the release field,
   revert that change before committing.

   **For Go packages that vendor deps:** When a CVE targets a
   vendored module, create the patch against `go.mod`+`go.sum`.
   Use `PatchN:` — **not** `dependency_overrides` or `pre_commands`
   (`go get ...`) in `go-vendor-tools.toml`. See
   `documentation/operating/rebuilding-packages.md` step 4 ("For Go
   packages: check go-vendor-tools.toml's pre_commands") for the
   full procedure. This applies regardless of whether the package is
   migrated to gorget:

   - **`pre_commands`/`dependency_overrides` only run against the
     vendor archive's own checkout** — the plain source tarball
     (`Source0`) never sees them, so `go.mod` in the build tree and
     `vendor/modules.txt` in the vendor archive can require different
     versions of the same package, which `go build -mod=vendor`
     rejects as inconsistent vendoring. See "Known sharp edge:
     patch-list duplication" in
     `documentation/design/source-pipeline-tool.md` for the trivy
     incident this rule comes from.
   - **They require live network access** (`go get`), so they can
     only run during gorget's own fetch/vendor stage, never in
     `%prep` (Konflux builds are hermetic, no network) — a `PatchN`
     is the only form of this fix `%prep` can apply.

   `test/test_govendortools_gomod_patch_sync.py` checks for this
   drift, but treat it as a safety net, not a substitute for doing
   this yourself. After patching, regenerate the vendor tarball with
   `go-vendor-tools`, copy it to `/tmp`, and print the lookaside
   upload command (same as the version-bump path step 7). Do not
   upload unless the user asks.

4. **Commit, validate, build, push, MR** (same as the
   version-bump path step 9).

#### 3f: No upstream fix yet -- `cve-next-release`

When no fix exists upstream (or a fix exists but is not yet in a
release we can consume):

1. Add a comment that Hummingbird is affected and waiting on an
   upstream fix.
2. Label the ticket:

   ```bash
   python .cursor/skills/cve/cve_helper.py next-release HUM-XXXX \
     -m "Affected; waiting on an upstream fix."
   ```

3. Leave the ticket In Progress. Do not create a HUM task unless
   active investigation or a custom patch is planned.
4. Rename the chat (see Step 1) with `--title-prefix "next-rel"`.

<!-- markdownlint-enable MD029 -->

### Step 4: Comment in the ticket

Always add a comment with the analysis results. The comment should
include whichever of the following apply:

- Why the package is or is not affected
- Version details (CVE range, Hummingbird SRPM version, comparison)
- Upstream fix commit verification (hash, URL, date, release tag)
- Vendored dependency check results
- SBOM evidence (Jira `{nvr}.sbom.json` attachment and/or Pulp
  URL/file, match terms, runtime vs build-time determination)
- Version-scheme notes for dotnet/Go packages

Use Jira wiki markup in comments (`{noformat}`, `*bold*`,
`{{monospace}}`).

```bash
python .cursor/skills/cve/cve_helper.py comment HUM-XXXX \
  -f /tmp/cve-comment.txt
```

## Resolution and VEX justification reference

| Scenario | Resolution | VEX Justification |
| --- | --- | --- |
| CVE targets a different product | Not a Bug | Vulnerable Code not Present |
| CVE product not in Hummingbird | Not a Bug | Component not Present |
| CVE disputed by upstream | Not a Bug | Vulnerable Code not Present |
| SRPM version above fix version | Done-Errata | (not set) |
| Vendored dep fixed | Done-Errata | (not set) |
| Backport patch applied | Done-Errata | (not set) |
| Duplicate of versioned ticket | Duplicate | (not set) |
| Affected, no upstream fix yet | In Progress | (not set; `cve-next-release`) |

## Important rules

1. **Never close Done-Errata tickets directly.** Set Fixed in Build
   and let the automation open the advisory MR and close the
   ticket. Do not follow the manual advisory steps in
   `cve-manual-process.md` Step 8 unless the user asks or
   automation cannot proceed (e.g. `advisory-mr-failed`).

2. **Always ask before closing any ticket.** Closing requires
   explicit user approval.

3. **Use `--status Closed`, not `--close`.** The `--close` flag
   does not reliably transition ticket status.

4. **Create HUM tasks** for any backport or update work. Link them
   to the CVE tracker(s) with blockers.

5. **Use `cve_helper.py` for Jira writes.** It imports
   `hummingbird_cve_analysis.lib.jira_client` and loads credentials
   from the environment or `~/.config/rhjira/agent.env`. Do not use
   the Atlassian MCP, and do not paste REST or `rhjira` write recipes
   into the session.

6. **Do not paste a retry helper into the session.** `cve_helper.py`
   already retries transient Jira/proxy failures. For raw `rhjira`
   writes, run once with `--noeditor`. On login failure, source
   `~/.config/rhjira/agent.env` once and retry once. Do not use
   unbounded retries or long sleep/poll loops.

7. **Label changes go through `cve_helper.py next-release`** (or
   `jira_client.add_jira_label` / `remove_jira_label`). Do not tell
   the user to use the Jira UI for labels.

8. **Always pass `--noeditor` on Jira writes.** This includes comment,
   transition, and field-update operations.

9. **Do not close mismatch tickets without SBOM evidence**, unless
   Step 2a (spec-deps) shows no bundled reference to the component —
   in that case spec-deps absence is the evidence and SBOM verification
   can be skipped. For all other `Component not Present` decisions,
   verify component presence in SBOM and determine runtime-installed vs
   build-time-only before closing (unless the user explicitly
   approves an SBOM-unavailable override).

10. **Comment first, then act.** Always document analysis before
   changing ticket state or fields.

11. **Do NOT add `%changelog` entries to spec files.** The CI
   validation rejects local changelog entries.

12. When the user provides the package name and multiple HUM ticket
    keys together (e.g. "let's look at ruby4.0 HUM-2648 HUM-2645"),
    investigate all **named** tickets for that package as a batch.
    Same-CVE tickets discovered by find-related stay context until
    the user confirms them. `only` means named tickets only.

13. **`modification_reason` must identify the CVE**, and only for
    Fedora-imported packages that are (or become) `modified`.
    Set `--reason` to a short explanation that includes the CVE
    ID (e.g. `"Backport CVE-YYYY-NNNNN"`), matching
    `documentation/operating/package-metadata-fields.md`. Append
    additional CVEs with `; CVE-YYYY-MMMMM`. Never add
    `modification_reason` to an `independent` package.

14. **Metadata `release` follows
    `documentation/operating/package-metadata-fields.md`.** Do
    not invent a CVE-specific rule; never change it for FIB or
    advisory reasons alone.

## Worktree cleanup

Worktrees share the same `.git` object store as the main repo, so
commits made in a worktree are visible everywhere. After the MR is
merged or the worktree is no longer needed, clean up from the main
repo:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
git worktree remove "${REPO_ROOT}/../worktrees/HUM-YYYY"
```

Then move the agent back to the main repo:

```text
CallMcpTool: cursor-app-control / move_agent_to_root
  rootPath: <absolute path to main repo>
```

To list active worktrees:

```bash
git worktree list
```
