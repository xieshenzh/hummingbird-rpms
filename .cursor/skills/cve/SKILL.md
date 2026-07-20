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
download the SBOM to confirm whether the component is present:

```bash
curl -sL "https://packages.redhat.com/api/pulp-content/public-hummingbird/metadata/sboms/<package>-main/" \
  | grep -oP 'href="(sha256-[^"]+\.sbom)"' | tail -1
```

Then download and search for the CVE product in the SBOM JSON.

#### 2e: Upstream fix verification

**CRITICAL:** Never conclude "fixed" based solely on the Hummingbird
SRPM version being numerically higher than a distro's patched
version. The fix may have been committed to upstream *after* the
Hummingbird SRPM's release tag.

When recommending Done-Errata:

1. Identify the upstream fix commit
1. Compare its date against the SRPM's upstream release tag date
1. Only confirm fixed if the fix commit predates the release tag

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

#### 3a: Already fixed -- set Fixed in Build

When the fix is confirmed present in the shipped SRPM:

1. Add a comment documenting the evidence (patches applied,
   version comparison, upstream commit dates)
1. Set Fixed in Build:

   ```bash
   rhjira edit HUM-XXXX --noeditor \
     --fixedinbuild "<name>-<version>-<release>.src.rpm"
   ```

1. **Do NOT close the ticket.** The cve_analysis automation will
   close it as Done-Errata automatically. The user has consistently
   said "we can wait for the automation to pick this up."

#### 3b: Not affected / misfiled -- close as Not a Bug

When the CVE does not apply (wrong product, component not present,
disputed):

1. Write a closing comment to a temp file explaining why
1. Post the comment:

   ```bash
   rhjira comment HUM-XXXX --noeditor -f /tmp/close-comment.txt
   ```

1. **Ask the user for approval before closing.** Then use the
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

1. Verify: `rhjira show HUM-XXXX 2>&1 | grep "^Status:"`

#### 3c: Duplicate (unversioned package)

When a ticket is filed against an unversioned base name (e.g.
`ruby`) but versioned SRPMs exist (e.g. `ruby3.3`, `ruby4.0`):

1. Search for versioned tickets:

   ```bash
   rhjira list "project = HUM and summary ~ CVE-YYYY-NNNNN" \
     --rawoutput --numentries 10 --fields key,summary,status
   ```

1. If versioned tickets exist, recommend closing as Duplicate

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

1. **Create a worktree** for the task. This keeps each CVE
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

1. **Download and verify the new tarball** -- download the
   tarball and signature, then verify the GPG signature. If
   the signing key changed between releases, update `Source2:`
   in the spec and `git rm` the old key file.

1. **Check existing patches** -- for each `PatchN:` in the
   spec, try `patch --dry-run -p1` against the extracted new
   source. Drop patches that were upstreamed: remove the
   `PatchN:` line from the spec and `git rm` the patch file.

1. **Update the spec file:**
   - `Version:` to the new version
   - `Release:` -- use `0.1%{?dist}` when ahead of Fedora
     (sorts below Fedora's eventual `1%{?dist}`). If Fedora
     already has this version, use the `.1` suffix on their
     release number instead.
   - Update `Source2:` if the GPG signing key changed

1. **Update supporting files:**
   - `sources` -- SHA512 checksums for the new tarball and
     signature (see `documentation/operating/lookaside-cache-access.md`)
   - `.gitignore` -- update the version glob pattern if the
     new version falls outside the existing range
   - `metadata/<package>.json` -- update `version` and
     `release` to match the spec

1. **Lookaside and build pipeline setup (when ahead of
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

1. **Mark the package as modified.** If the metadata already has
   a `modification_reason`, read it first and **append** to it
   rather than replacing it.

   ```bash
   ./ci/dist_git.py mark-modified <package> --modified \
     --reason "<existing reason>; update to <version> for CVE-YYYY-NNNNN"
   ```

1. **Commit, validate, build, push, MR.** Commit with a
   descriptive message, then:

   ```bash
   make check
   ```

   ```bash
   ./ci/build_rpms.sh <package>
   ```

   Push the branch and create a draft MR. The MR description
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

   ```bash
   git push -u origin HUM-YYYY
   cat > /tmp/mr-description.txt << 'EOF'
   HUM-YYYY: <package>: <short title>

   <1-3 sentence description of the change: what was backported
   or updated, where the fix came from, and why>

   Closes: HUM-YYYY
   Ref: HUM-XXXX, HUM-ZZZZ
   CVE: CVE-YYYY-NNNNN, CVE-YYYY-MMMMM
   EOF
   lab mr create --draft -F /tmp/mr-description.txt
   ```

   Add the MR link as a comment on the HUM task ticket so
   future lookups can see the work is already in review:

   ```bash
   rhjira comment HUM-YYYY --noeditor \
     -m "MR: https://gitlab.com/redhat/hummingbird/rpms/-/merge_requests/NNNN"
   ```

   After the MR pipeline has started, trigger the automated code
   review. Check the pipeline status first:

   ```bash
   lab ci status !NNNN
   ```

   Once the pipeline is running, trigger the review:

   ```bash
   lab mr comment !NNNN -m "/hummingbird code-review"
   ```

1. **Close the task ticket.** After the MR is created (do not
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

1. **Backport the patch** following the process in
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

1. **Mark the package as modified.** If the metadata already has
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

1. **Commit, validate, build, push, MR** (same as 3d step 9).

#### 3f: No upstream fix yet

When no fix exists upstream, add a comment noting the current
status and leave the ticket in its current state. Create a HUM
task only if active investigation or a custom patch is planned.

### Step 4: Comment in the ticket

Always add a comment with the analysis results. The comment should
include whichever of the following apply:

- Why the package is or is not affected
- Version details (CVE range, Hummingbird SRPM version, comparison)
- Upstream fix commit verification (hash, URL, date, release tag)
- Vendored dependency check results
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

1. **Always ask before closing any ticket.** Closing requires
   explicit user approval.

1. **Use `--status Closed`, not `--close`.** The `--close` flag
   does not reliably transition ticket status.

1. **Create HUM tasks** for any backport or update work. Link them
   to the CVE tracker(s) with blockers.

1. **Use rhjira** for all Jira operations. Do not use the Atlassian
   MCP or other tools.

1. **rhjira edit does NOT support label changes.** Label additions
   or removals must be done manually in the Jira web UI.

1. **Comment first, then act.** Always document analysis before
   changing ticket state or fields.

1. **Do NOT add `%changelog` entries to spec files.** The CI
   validation rejects local changelog entries.

1. When the user provides the package name and multiple HUM ticket
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
