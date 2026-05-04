---
name: upstream-diff
description: >-
  Analyze diffs between local RPM packages and upstream Fedora/CentOS to
  determine which modifications are candidates for upstreaming. Use when the
  user asks to review package diffs, check what can be upstreamed, analyze
  modifications, or says /upstream-diff.
---

# Upstream Diff

Classify locally modified RPM packages as upstreamable, hummingbird-specific,
or complex. All deterministic operations are handled by `./ci/upstream_diff.py`.

If invoked with **no arguments**, ask the user:

1. **Analyze** — next batch of unanalyzed/stale packages
2. **View results** — show cached analysis summary
3. **JIRA** — create/update a JIRA issue for a package

## Analyze

### Step 1: Prepare

```bash
# For specific packages:
./ci/upstream_diff.py prepare <package1> <package2> ...

# For next batch of unanalyzed packages:
./ci/upstream_diff.py prepare --batch 10
```

The output contains everything needed: package metadata, diffs,
upstream PR status, category definitions, and the classification schema.

### Step 2: Classify

For each package in the prepare output, apply the classification
schema using the embedded category definitions. The only judgment
needed is selecting the category and writing the reasoning fields.

Factor in **upstream PRs** shown in the prepare output:

- If a merged PR already covers the local change, note it in the
  recommendation (e.g., "Already merged upstream as PR #N —
  update local package and unmark as modified").
- If an open PR already covers the local change, note it in the
  recommendation (e.g., "Already submitted upstream as PR #N —
  monitor for merge").
- If a PR covers part of the change, mention which parts are
  already in flight.

Packages with empty diffs should be classified as `no-diff`.

### Step 3: Save

For each classified package, save the results. Include the IDs of
any upstream PRs that are **related** to our local changes (determined
during Step 2). Pass `""` if no PRs are related.

```bash
./ci/upstream_diff.py save <package> \
  --category <category> \
  --changes-summary "<summary>" \
  --reasoning "<reasoning>" \
  --recommendation "<recommendation>" \
  --upstream-prs <pr_id1> <pr_id2> ... \
  [--hummingbird-macros]

# No related PRs:
./ci/upstream_diff.py save <package> \
  --category <category> \
  --changes-summary "<summary>" \
  --reasoning "<reasoning>" \
  --upstream-prs ""
```

The script enforces template requirements (e.g., appending standard
language for hummingbird-specific packages). Repeat for each package.

### Step 4: Report

After saving all packages, run `./ci/upstream_diff.py view` and
show the output to the user.

## View Results

```bash
# All packages:
./ci/upstream_diff.py view --markdown

# Single package detail:
./ci/upstream_diff.py view --markdown <package>

# Filter by category:
./ci/upstream_diff.py view --markdown --category <category>

# Include unanalyzed packages:
./ci/upstream_diff.py view --markdown --all
```

Run the appropriate command and show the output to the user.

## Check Upstream PRs

```bash
./ci/upstream_diff.py check-prs <package1> <package2> ...
```

Shows open and recently merged (last 90 days) pull requests in
the upstream Fedora dist-git repo. Use this to check whether
local changes have already been submitted or merged upstream.

## Update Related PRs

To update which PRs are related after the initial analysis,
use `check-prs` to see all current PRs, then update the
stored related PRs:

```bash
# See all current upstream PRs:
./ci/upstream_diff.py check-prs <package>

# Update related PRs on existing entry:
./ci/upstream_diff.py save --set-upstream-prs <package> <pr_id1> <pr_id2> ...

# Clear related PRs (none are related):
./ci/upstream_diff.py save --set-upstream-prs <package>
```

## JIRA

The package must already be analyzed (cached). If not, run analysis first.

### Step 1: Generate templates

If the package has no related PRs stored but should, run
`check-prs` then `save --set-upstream-prs` first to populate
the cached PR data.

```bash
./ci/upstream_diff.py jira-template <package> [--epic HUM-1613]
```

This outputs pre-filled JIRA description, analysis comment, and
(if upstream PRs exist) a separate PR comment — all populated
from the cache.

### Step 2: Search for existing issues

Use the JIRA MCP to search:

```jql
project = HUM AND summary ~ "<package>" AND summary ~ "upstream"
```

### Step 3: Create or update

**No existing issue:** Preview the description and comment from
Step 1 to the user. Wait for explicit confirmation. Create with
`jira_create_issue` (project HUM, type Story, parent from template).
Add the analysis as a comment with `jira_add_comment`. If the
template includes a PR comment section, add it as a **second**
`jira_add_comment` call.

Record the issue key:

```bash
./ci/upstream_diff.py save --set-jira <package> HUM-XXXX
```

**Existing issue:** Compare template output against existing content.
If diverged, preview updates and wait for explicit confirmation.
Update with `jira_update_issue` and/or `jira_edit_comment`. If
upstream PRs have changed since the last update, add a **new**
comment with the PR data (do not edit the analysis comment).
