---
name: cve
description: >-
  Investigate and resolve individual HUM CVE tracker tickets. Covers
  showing ticket details, determining if Hummingbird is affected,
  backporting patches, setting Fixed in Build, and closing misfiled
  tickets. Use when the user says /cve, "let's look at HUM-XXXX"
  (a CVE tracker), or asks to investigate/fix a specific CVE.
---

# CVE Tracker Investigation

Investigate individual HUM CVE tracker tickets, determine whether
Hummingbird is affected, and take the appropriate resolution action.

This skill is for hands-on, per-ticket work. For bulk triage of
`cve-needs-attention` tickets, use `/analyse-cve-needs-attention`.
For a high-level overview, use `/cve-status`.

## Procedure

### Step 0: Run Jira commands reliably (HUM-4821)

Use `rhjira` directly for all Jira reads and writes. Do not wrap
routine `/cve` Jira operations in Python subprocess wrappers,
background polling workers, or long-running retry loops.

`/cve` workflows require outbound internet access for tools like
`rhjira`, `glab`, and `curl` (Jira/GitLab/SBOM endpoints). Running
these commands in a sandboxed/no-network context can produce false
failures (auth/proxy/timeout/connection errors) and block ticket
automation. Use an internet-enabled execution context for these tool
calls.

Use this bounded retry helper for transient Jira/proxy failures:

```bash
rhjira_retry() {
  local max_attempts=3
  local backoff=2
  local attempt=1 output rc

  while [ "$attempt" -le "$max_attempts" ]; do
    output="$(rhjira "$@" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      printf '%s\n' "$output"
      return 0
    fi

    if ! printf '%s\n' "$output" | grep -qiE \
      "proxy|tunnel|timed out|timeout|temporar|502|503|504|connection reset|eof"; then
      printf '%s\n' "$output" >&2
      return "$rc"
    fi

    if [ "$attempt" -eq "$max_attempts" ]; then
      printf 'ERROR: rhjira failed after %s attempts: rhjira %s\n' "$max_attempts" "$*" >&2
      printf '%s\n' "$output" >&2
      return "$rc"
    fi

    printf 'WARN: transient Jira/proxy error (attempt %s/%s); retrying in %ss\n' \
      "$attempt" "$max_attempts" "$backoff" >&2
    sleep "$backoff"
    backoff=$((backoff + 2))
    attempt=$((attempt + 1))
  done
}
```

Rules for using the helper:

1. Use `rhjira_retry` for direct Jira calls.
2. For write operations (comment, status/resolution changes, field
   updates), always pass `--noeditor`.
3. Avoid long polling loops (`while ... sleep 30`). After a write,
   do at most one verify read through `rhjira_retry`; if Jira is
   still unavailable, fail fast.
4. Report per-ticket Jira status clearly before stopping, for
   example:

   ```text
   HUM-1234: jira_unavailable (proxy tunnel 403 after 3 attempts; no changes applied)
   HUM-1235: read_ok
   ```

### Step 1: Show the ticket(s)

If the user provides bare numbers (e.g. "2875" or "/cve 2875"),
treat them as HUM tickets by prepending `HUM-`.

```bash
python .cursor/skills/cve/cve_helper.py HUM-XXXX
```

For multiple tickets, pass them in one call:

```bash
python .cursor/skills/cve/cve_helper.py HUM-1234 1235 1236 \
  --json-out /tmp/cve-triage.json
```

**Finding Related Tickets:** `cve_helper.py` automatically discovers all open
HUM tickets with the same CVE IDs — this is on by default so you get the full
set in one invocation. Use `--no-find-related` to opt out:

```bash
# Default: shows HUM-1234 + any open tickets sharing its CVEs
python .cursor/skills/cve/cve_helper.py HUM-1234

# Opt out: show only HUM-1234
python .cursor/skills/cve/cve_helper.py HUM-1234 --no-find-related
```

This is especially useful for batch resolution across versioned packages
(e.g., ruby3.3, ruby4.0, llvm, llvm21) — investigate once, apply to all.
The related tickets are fetched during the initial show so there's no need for
a second invocation.

Use the script output as the primary source for:

- CVE IDs and package guess
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
FIB values already set. Summarize relevant context — if it's already been
resolved, say so rather than re-doing the work.

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
4. **Batch actions by outcome:**
   - **Same resolution:** Write one comment template, post to each ticket in a loop,
     create one task linking all trackers, set FIB on each if applicable.
   - **Mixed outcomes:** Handle each group independently. Close unaffected ones
     directly, create tasks for those needing fixes.

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

#### 2a: Version comparison

Compare the Hummingbird SRPM version against the CVE affected range.
If the SRPM version is above the fix version, the package *may* be
fixed -- but see the critical rule in Step 2e before concluding.

#### 2b: Code inspection

Check the Hummingbird package source for the vulnerable code:

```bash
ls rpms/<package>/
```

Read the spec file and any existing patches. If patches addressing
the CVE are already applied, the package is fixed.

#### 2c: Vendored dependency check (Go packages)

The `cve_helper.py` output already shows `Vendored deps: yes (go-vendor-tools)`
for any package with a `go-vendor-tools.toml` — use this as a first check.

For module-level presence, use SBOM verification (Step 2d) which identifies
the specific vendored module, its version, and whether it's runtime-installed.

If not vendored by any package, the ticket is misfiled.

#### 2d: Spec file bundled dependency check

For product-mismatch CVEs, check the spec file for bundled dependencies:

```bash
grep -i "<component-name>" rpms/<package>/<package>.spec
```

Look for `Provides: bundled(...)` lines showing vendored dependencies
and their versions. If found, compare against the CVE affected range.

#### 2e: SBOM verification

When the CVE product differs from the Hummingbird package name,
download and inspect the SBOM to determine:

- whether the CVE product/component is present at all
- whether it is runtime-installed in shipped binary RPMs
- or only a build-time/test-time dependency

```bash
SBOM_BASE="https://packages.redhat.com/api/pulp-content/public-hummingbird/metadata/sboms/<package>-main/"
SBOM_FILE=$(curl -fsSL "$SBOM_BASE" | rg -o 'sha256-[^"]+\.sbom' | sort -u | tail -1)
curl -fsSL "${SBOM_BASE}${SBOM_FILE}" -o /tmp/<package>.sbom.json
```

Then inspect SBOM contents for the CVE product/component:

```bash
rg -ni "<cve-product>|<module>|<library-name>" /tmp/<package>.sbom.json
```

When the component is found, determine whether it is actually
installed in shipped binary RPMs vs only used during build/test.
Use SBOM fields such as `type`, `scope`, `purl`, `properties`,
`metadata.component`, and package relationships.

If the component's role is unclear from SBOM metadata, search to clarify its purpose.

Decision guidance:

- If component is present in runtime binary package contents,
  treat as potentially affected.
- If component appears only in build/test toolchain paths and is
  not present in installed runtime binary RPM contents, treat as
  not runtime-affected.
- If SBOM evidence is ambiguous, do not close the ticket based on
  component absence alone; continue manual investigation.

For any `Not a Bug` recommendation based on product mismatch,
include SBOM evidence in the Jira comment:

- SBOM URL/file used
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
resolution to the user**:

- Which resolution path applies (e.g., "3a: Already fixed — set FIB",
  "3b: Not affected — close as NAB", "3d: Needs version bump")
- Why (brief justification based on your investigation)
- What evidence supports it (version comparison, SBOM findings, code
  inspection results, upstream fix verification)

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
   rhjira edit HUM-XXXX --noeditor \
     --fixedinbuild "<name>-<version>-<release>.src.rpm"
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
   verification first (Step 2d) and capture runtime-vs-build-time
   evidence for binary RPM installation status.
2. **Name the specific component** extracted from the CVE description
   in the closing comment. State why it was checked and why it is
   absent from the Hummingbird package.
3. Write a closing comment to a temp file explaining why
4. Post the comment:

   ```bash
   rhjira comment HUM-XXXX --noeditor -f /tmp/close-comment.txt
   ```

5. **Ask the user for approval before closing.** Then use the
   appropriate VEX justification:

   ```bash
   # If the CVE product is not in Hummingbird at all:
   rhjira edit HUM-XXXX --noeditor \
     --assignee <user>@redhat.com \
     --status Closed \
     --resolution "Not a Bug" \
     --vexjustification "Component not Present"

   # If the CVE targets a different product or is disputed:
   rhjira edit HUM-XXXX --noeditor \
     --assignee <user>@redhat.com \
     --status Closed \
     --resolution "Not a Bug" \
     --vexjustification "Vulnerable Code not Present"
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
   REPO_ROOT=$(git rev-parse --show-toplevel)
   mkdir -p "${REPO_ROOT}/../worktrees"
   git worktree add "${REPO_ROOT}/../worktrees/HUM-YYYY" \
     -b HUM-YYYY main
   ```

   Then move the agent workspace into the worktree:

   ```text
   CallMcpTool: cursor-app-control / move_agent_to_root
     rootPath: <absolute path to worktree, from command above>
   ```

   All subsequent file operations (spec edits, tarball downloads,
   `mark-modified`, `build_rpms.sh`) happen inside the worktree.

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
     version. Metadata `release` is for `dist_git.py`
     update/rebuild bookkeeping (base release for `.N`
     micro-bumps), **not** for CVE automation
     (`cve_analysis.py` ignores it). When ahead of Fedora, set
     it to the local base release without a dist tag (typically
     `0.1`, matching spec `0.1%{?dist}`). When Fedora already
     has this version, set it to the Fedora baseline (no dist
     tag). See
     `documentation/operating/package-metadata-fields.md`.

7. **Lookaside and build pipeline setup (when ahead of
   Fedora):** When Fedora has not yet released this version,
   the tarball must be served from the Hummingbird lookaside
   cache instead of Fedora's. Three things are required:
   - Upload the tarball (and signature if listed in `sources`)
     to the Hummingbird lookaside:

     ```bash
     ./ci/upload-to-lookaside-cache.sh -f <tarball> -p <package>
     ./ci/upload-to-lookaside-cache.sh -f <sig-file> -p <package>
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
     (see `documentation/operating/adding-native-packages.md`
     step 5).
   - Include `.tekton/` and `package-overrides.yaml` in the
     commit.

8. **Mark the package as modified.** Set `modification_reason` to
   the CVE ID only (no descriptive prose). If the metadata already
   has a `modification_reason`, read it first and **append** the
   CVE ID to it rather than replacing it. For multiple CVEs fixed
   in the same change, append each ID (semicolon-separated).

   ```bash
   # First CVE fix for this package:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "CVE-YYYY-NNNNN"

   # Package already modified — append the CVE ID:
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
   - **Bump the spec `Release:` field correctly.** Use metadata
     `release` only as the Fedora base for computing the next
     `.N` micro-bump (same value `dist_git.py rebuild` uses).
     CVE automation does not read this field.

     | Metadata release | Current spec Release | New spec Release |
     | --- | --- | --- |
     | `1` | `1%{?dist}` | `1.1%{?dist}` |
     | `1` | `1.1%{?dist}` | `1.2%{?dist}` |
     | `1` | `1.2%{?dist}` | `1.3%{?dist}` |
     | `5` | `5%{?dist}` | `5.1%{?dist}` |
     | `5` | `5.2%{?dist}` | `5.3%{?dist}` |

     The integer part **must** match the metadata `release` value.
     Never increment the integer (e.g. `1` to `2`). Only the
     micro-bump after the dot changes. If the spec already has a
     micro-bump, increment it; if not, add `.1`.

   - If the spec uses `%patch N -p1`, use that form; if
     `%autosetup -p1`, patches apply automatically

3. **Mark the package as modified.** Set `modification_reason` to
   the CVE ID only (no descriptive prose). If the metadata already
   has a `modification_reason`, read it first and **append** the
   CVE ID to it rather than replacing it. For multiple CVEs fixed
   in the same change, append each ID (semicolon-separated).

   ```bash
   # First CVE fix for this package:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "CVE-YYYY-NNNNN"

   # Package already modified — append the CVE ID:
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; CVE-YYYY-NNNNN"
   ```

   **Do NOT change the metadata `release` field for backports.**
   Leave it at the Fedora base so `dist_git.py rebuild` can
   compute the next `.N` suffix. This field is dist-git
   bookkeeping, not a CVE/FIB input. If `mark-modified` changes
   it, revert that change before committing.

   **For Go packages that vendor deps:** When a CVE targets a
   vendored module, create the patch against `go.mod`+`go.sum`.
   Use `PatchN:` (not `dependency_overrides` in
   `go-vendor-tools.toml`) — patches persist across version
   updates; overrides don't. After patching, regenerate the
   vendor tarball with `go-vendor-tools` and upload to lookaside.

4. **Commit, validate, build, push, MR** (same as 3d step 9).

#### 3f: No upstream fix yet

When no fix exists upstream, add a comment noting the current
status and leave the ticket in its current state. Create a HUM
task only if active investigation or a custom patch is planned.

<!-- markdownlint-enable MD029 -->

### Step 4: Comment in the ticket

Always add a comment with the analysis results. The comment should
include whichever of the following apply:

- Why the package is or is not affected
- Version details (CVE range, Hummingbird SRPM version, comparison)
- Upstream fix commit verification (hash, URL, date, release tag)
- Vendored dependency check results
- SBOM evidence (URL/file, match terms, runtime vs build-time
  determination)
- Version-scheme notes for dotnet/Go packages

Use Jira wiki markup in comments (`{noformat}`, `*bold*`,
`{{monospace}}`).

```bash
cat > /tmp/cve-comment.txt << 'EOF'
<comment text>
EOF
rhjira comment HUM-XXXX --noeditor -f /tmp/cve-comment.txt
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

5. **Use `rhjira` directly** for all Jira operations. Do not use the
   Atlassian MCP or Python subprocess wrappers for routine `/cve`
   ticket work.

6. **Use bounded retries only for transient failures.** Use
   `rhjira_retry` with short backoff (2s, 4s) and a 3-attempt cap.
   Do not use unbounded retries or long sleep/poll loops.

7. **rhjira edit does NOT support label changes.** Label additions
   or removals must be done manually in the Jira web UI.

8. **Always pass `--noeditor` on Jira writes.** This includes comment,
   transition, and field-update operations.

9. **Do not close mismatch tickets without SBOM evidence.** For
   `Component not Present` or related mismatch decisions, verify
   component presence in SBOM and determine runtime-installed vs
   build-time-only before closing (unless the user explicitly
   approves an SBOM-unavailable override).

10. **Comment first, then act.** Always document analysis before
   changing ticket state or fields.

11. **Do NOT add `%changelog` entries to spec files.** The CI
   validation rejects local changelog entries.

12. When the user provides the package name and multiple HUM ticket
    keys together (e.g. "let's look at ruby4.0 HUM-2648 HUM-2645"),
    investigate all tickets for that package as a batch.

13. **`modification_reason` must contain only CVE IDs.** When
    marking a package modified for a CVE fix (version bump or
    backport), set `--reason` to the CVE ID alone (e.g.
    `CVE-YYYY-NNNNN`). Append additional CVE IDs with
    `; CVE-YYYY-MMMMM`. Do not include prose such as "update to
    …" or "backport fix for …" — scripts and automation parse
    this field for CVE IDs.

14. **Metadata `release` is not a CVE field.** It tracks the base
    release for `dist_git.py` update/rebuild. `cve_analysis.py`
    ignores it. Only adjust it when 3d/3e package changes require
    it; never for FIB or advisory reasons alone.

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
