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

Use the script output as the primary source for:

- CVE IDs and package guess
- Status, severity, labels, fixed-in-build
- Linked HUM tickets and linked task MR URLs
- cve_analysis `{noformat}` excerpt
- `suggested_chat_title` (deterministic title for `rename_chat`)

Do not run raw `rhjira show`/`rhjira dump` for Step 1 when the helper succeeds.
If helper output is missing required detail or the helper fails, report the helper
failure to the user and ask whether to proceed with manual fallback commands.

**EMBARGO CHECK:** If the ticket summary starts with `EMBARGOED`,
stop immediately and output:

```text
WARNING: HUM-XXXX is EMBARGOED. Embargoed CVEs must not be
discussed, analyzed, or acted upon in this tool. Stopping.
```

Do not proceed with any analysis, comments, or code changes.

Extract from each ticket (mostly from `cve_helper.py` output):

- CVE ID and package name (from summary)
- Status, severity, labels
- Automated analysis comment (the `{noformat}` block from the
  cve_analysis bot -- see the `/analyse-cve-needs-attention` skill
  for parsing details)
- Hummingbird SRPM version
- CVE affected version range
- Upstream fix info (commits, PRs)
- Fixed-in-build field (if already set)
- Issue links (look for blocking Task tickets with MR links in
  their comments -- this means work is already in progress or
  under review)

If a linked Task ticket already has an MR posted, tell the user
the work is already done and point them to the MR. Do not
duplicate the effort.

**Review all comments** on the ticket (not just the cve_analysis
bot comment). Previous human or agent comments may contain:

- Prior analysis or investigation results
- Upstream fix status updates
- Decisions about whether to backport or wait
- Fixed in Build values already set
- Links to related MRs or upstream PRs

Summarize any relevant prior comments so the user has full
context before deciding next steps. If previous analysis already
resolved the question (e.g. "already fixed in build X"), say so
rather than re-doing the work.

Present a concise summary to the user and wait for direction.

After showing the ticket(s), rename the chat using
`suggested_chat_title` from `cve_helper.py`. This value is
deterministic and already follows the required naming format.
Only construct the title manually if `suggested_chat_title` is
empty.

```text
CallMcpTool: cursor-app-control / rename_chat
  title: "HUM-XXXX <package>"
```

### Step 2: Determine if Hummingbird is affected

The user will typically ask to investigate. Use whichever of these
approaches fits the situation:

**Web search efficiency:** Check local sources first—ticket
description, cve_analysis comment, local code, SBOM, metadata.
The Jira `Description` field usually names the specific vulnerable
sub-component; extract it early (`rhjira dump`) as it often
resolves mismatch cases without any web search.

When you do search:

- **Product mismatch** (CVE vendor/product differs from Hummingbird package):
  Search for architectural/implementation differences between the products,
  not CVE details. Example: `"<packageA> vs <packageB>" <component> differences`
- **Component presence**: Search whether the package implements the vulnerable
  component, not the CVE ID itself
- **Avoid CVE ID searches**: Recent CVEs are often not yet indexed in
  NVD/CVE.org; search product/component relationships instead

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

For CVEs against Go modules filed on `golang*` packages, check
whether any Hummingbird package vendors the module:

```bash
rg -i "<module-name>" ci/vendored_deps.csv
```

If vendored, identify which packages carry it and what version.
If not vendored by any package, the ticket is misfiled.

#### 2d: SBOM verification

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

#### 2e: Upstream fix verification

**CRITICAL:** Never conclude "fixed" based solely on the Hummingbird
SRPM version being numerically higher than a distro's patched
version. The fix may have been committed to upstream *after* the
Hummingbird SRPM's release tag.

When recommending Done-Errata:

1. Identify the upstream fix commit
2. Compare its date against the SRPM's upstream release tag date
3. Only confirm fixed if the fix commit predates the release tag

#### 2f: Known version-scheme issues

**dotnet packages:** SRPM uses SDK versions (e.g. 9.0.116), CVEs
use runtime versions (e.g. 9.0.16). Mapping: SDK third component
mod 100 = runtime patch. See `/analyse-cve-needs-attention` for
full details.

**Go pseudo-versions:** e.g. `v0.0.0-20260507153023-abc123` means
a commit snapshot, not a proper release tag. Compare the commit
date and hash against the fix, not the version number.

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

3. Rename the chat title to indicate FIB was set:

   ```bash
   FIB_TITLE=$(python .cursor/skills/cve/cve_helper.py HUM-XXXX --title-only --title-prefix "FIB")
   ```

   ```text
   CallMcpTool: cursor-app-control / rename_chat
     title: "<value from FIB_TITLE, e.g. FIB HUM-6789 foo>"
   ```

4. **Do NOT close the ticket.** The cve_analysis automation will
   close it as Done-Errata automatically. The user has consistently
   said "we can wait for the automation to pick this up."

#### 3b: Not affected / misfiled -- close as Not a Bug

When the CVE does not apply (wrong product, component not present,
disputed):

1. For product-mismatch or component-absence cases, perform SBOM
   verification first (Step 2d) and capture runtime-vs-build-time
   evidence for binary RPM installation status.
2. Write a closing comment to a temp file explaining why
3. Post the comment:

   ```bash
   rhjira comment HUM-XXXX --noeditor -f /tmp/close-comment.txt
   ```

4. **Ask the user for approval before closing.** Then use the
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

5. Rename the chat title to indicate Not a Bug closure:

   ```bash
   NAB_TITLE=$(python .cursor/skills/cve/cve_helper.py HUM-XXXX --title-only --title-prefix "NAB")
   ```

   ```text
   CallMcpTool: cursor-app-control / rename_chat
     title: "<value from NAB_TITLE, e.g. NAB HUM-6789 foo>"
   ```

6. Verify: `rhjira show HUM-XXXX 2>&1 | grep "^Status:"`

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
   - `metadata/<package>.json` -- update `version` and
     `release` to match the spec

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

8. **Mark the package as modified.** If the metadata already has
   a `modification_reason`, read it first and **append** to it
   rather than replacing it.

   ```bash
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; update to <version> for CVE-YYYY-NNNNN"
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
   - **Bump the Release field correctly.** The metadata `release`
     field contains the Fedora base release number -- use that as
     the base. Append or increment a `.N` micro-bump suffix:

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

3. **Mark the package as modified.** If the metadata already has
   a `modification_reason`, read it first and **append** to it
   rather than replacing it.

   ```bash
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; backport fix for CVE-YYYY-NNNNN"
   ```

   **Do NOT change the metadata `release` field for backports.**
   The `release` field must stay at the Fedora base release value.
   If `mark-modified` changes the release field, revert that
   change before committing.

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
   and let the automation close them. The user has been clear about
   this pattern across many sessions.

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
