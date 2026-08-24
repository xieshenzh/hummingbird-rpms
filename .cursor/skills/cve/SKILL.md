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

Hands-on per-ticket work. For bulk `cve-needs-attention` triage use
`/analyse-cve-needs-attention`. For an overview use `/cve-status`.

Do not run `cve_analysis.py --resolve` from this skill. Consume the
bot's `{noformat}` comment; write through `cve_helper.py`.

## Helper

```bash
python .cursor/skills/cve/cve_helper.py deps
export HUMMINGBIRD_TOOLS_ROOT=/path/to/hummingbird/tools   # if not pip-installed
```

Auth is the existing rhjira file (`JIRA_TOKEN`, `JIRA_SERVER` → URL,
`JIRA_EMAIL` → Basic user). No second Jira token.

| Command | Use |
| --- | --- |
| `investigate HUM-XXXX` | Show + recap + bot-mrs + spec-deps + GitLab MR search |
| `investigate HUM-XXXX only` | Named tickets only (`--no-find-related`) |
| `comment` / `next-release` / `set-fib` / `close-nab` | Jira writes. `--kind nab/fib/analysis/next-release` fills wiki markup from flags or `--from-json`; `--print-only` / `--json` to review |
| `create-task --blocks HUM-XXXX [--worktree] --summary "…"` | HUM Task — `--blocks` takes one ticket; link extras with `rhjira edit TASK --blocks HUM-XXXX --noeditor` |
| `open-mr --task HUM-YYYY --tracker HUM-XXXX --cve CVE-… --title "…" --message "…"` | Fork push + `glab` MR (not `advisory_handler`); `--title` and `--message` are required |
| `lookaside-cmd -f FILE -p PKG` | Print upload commands; do not upload |
| `version-check PKG` | Local NVR vs CVE range / FIB / analysis NVR |
| `upstream-fix-age` | Fix-commit date vs upstream tag/release date |
| `release-bump PKG` | Next spec `.N` from metadata + spec; does not write |
| `worktree HUM-YYYY` | Isolated checkout under `../worktrees/` |
| `bot-mrs` / `sbom` / `spec-deps` / `show` | Individual probes |

Need internet for Jira, GitLab, and Pulp. On login failure the helper
loads `~/.config/rhjira/agent.env`. Do not paste retry loops.

## Procedure

1. **Embargo.** If the summary starts with `EMBARGOED`, stop. Do not
   analyze or write.
2. **Investigate.** `cve_helper.py investigate HUM-XXXX` (add `only` if
   the user said only those keys). Bare numbers are HUM tickets.
3. **Read the recap.** It leads with assignee (user vs bot), labels, and
   FIB. User-named tickets are in-scope. Discovered same-CVE siblings
   are context until the user says so. A "Yes please" on the named set
   is not approval for extras. If FIB and a task/MR already exist, leave
   for advisory automation. If FIB is set with no task/MR, ask whether
   to create the task.
4. **Wait** for the user's Step 3 direction.
5. **Act** with the matching helper (below). Comment before state
   changes. Ask before any close.

For product-mismatch CVEs, run `spec-deps` before version comparison or
SBOM. If the component is not in the spec, treat an SBOM hit as a false
positive and close as NAB (`Component not Present`) without further
SBOM work. If the spec has the component, inspect the source for the
vulnerable pattern; if that path is not used, close as NAB
(`Vulnerable Code not Present`).

### Resolution commands

**Already fixed (3a):** `version-check PKG --ticket HUM-XXXX` then
`upstream-fix-age --commit URL --tag TAG` (or `--commit-date` /
`--tag-date`). `comment --kind fib --package PKG --nvr NVR` (add
`--from-json` / `--commit` / `--tag` when you have them) then
`set-fib HUM-XXXX NVR --package PKG`.
Pulp must list the NVR unless the user confirms `--force`. Do not close
Done-Errata; advisory automation does that. Chat prefix `FIB`.

**Not affected (3b):** SBOM evidence for component-absence, unless
`spec-deps` already shows no bundled reference. Preview with
`comment --kind nab --print-only`, then
`close-nab HUM-XXXX --vex "Component not Present"|"Vulnerable Code not Present"`
with the same `--package` / `--component` / SBOM flags (or `--from-json`).
Chat prefix `NAB`.

**Duplicate versioned ticket (3c):** ask, then close as Duplicate
(still ask first).

**Version bump or backport (3d/3e):** prefer a version bump.
`create-task --worktree`, move the agent into that worktree, then follow
[Rebuilding Packages](../../../documentation/operating/rebuilding-packages.md)
and [package-metadata-fields.md](../../../documentation/operating/package-metadata-fields.md).
`release-bump PKG` prints the next spec `.N`; do not change metadata
`release` for a backport. Do not add `%changelog`. `lookaside-cmd` prints
copy/upload commands and waits. `open-mr` uses `Closes:` for the **task**,
`Ref:` for trackers, `CVE:` for IDs. Chat prefix `!${MR_IID}`.

If `dist_git.py update` reports *"upstream branch rawhide is retired"*,
retry with `--branch f44` (or `--branch f43`). The `--dry-run` flag is
top-level: `./ci/dist_git.py --dry-run update PKG`.

Always invoke `open-mr` from the **main repo**'s helper even when the
agent is inside a worktree:
`python /path/to/rpms/.cursor/skills/cve/cve_helper.py open-mr …`

**Multi-CVE same-package pattern:** when multiple CVEs share the same
package and fix version, use **one task and one MR** for all of them.
Pass the first tracker to `create-task --blocks`, then link the rest
with `rhjira edit TASK --blocks HUM-XXXX --noeditor`. Pass all tracker
keys and CVE IDs to `open-mr --tracker` and `--cve`.

**No upstream fix (3f):** `next-release HUM-XXXX --package PKG` (or
`--kind next-release` with `--cve` / `-m`). Leaves In Progress.
Chat prefix `next-rel`.

| Scenario | Resolution | VEX |
| --- | --- | --- |
| Different product / disputed | Not a Bug | Vulnerable Code not Present |
| Product not in Hummingbird | Not a Bug | Component not Present |
| SRPM / vendored dep / backport fixed | Done-Errata via FIB | (not set) |
| Duplicate of versioned ticket | Duplicate | (not set) |
| Affected, no upstream fix | In Progress | `cve-next-release` |

## Important rules

1. Never close Done-Errata yourself. Set FIB and leave the tracker open.
2. Always ask before closing any ticket.
3. Create HUM tasks for backport or update work; link them with blockers.
4. Use `cve_helper.py` for Jira writes (`jira_client` / `pulp` imports).
   Do not use the Atlassian MCP, `cve_analysis.py --resolve`,
   `advisory_handler`, or `gitlab_sync` for package MRs.
5. Do not paste retry helpers. Load `agent.env` once on auth failure.
6. Do not close mismatch tickets without SBOM evidence unless
   `spec-deps` shows no bundled reference (that is the evidence) or
   the user overrides.
7. Comment first, then change state.
8. Named tickets are the write set; `only` means named tickets only.
9. `modification_reason` must include the CVE ID for Fedora-imported
   packages that are (or become) `modified`. Never set it on
   `independent`.
10. Metadata `release` follows package-metadata-fields.md; do not change
    it for FIB/advisory reasons alone.

## Worktree cleanup

After the MR merges, from the main repo:

```bash
git worktree remove "$(git rev-parse --show-toplevel)/../worktrees/HUM-YYYY"
```

Then `move_agent_to_root` back to the main checkout.
