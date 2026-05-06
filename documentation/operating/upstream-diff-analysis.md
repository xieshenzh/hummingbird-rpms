---
title: Upstream Diff Analysis
weight: 60
aliases: [/l/upstream-diff-analysis]
---

## Overview

The upstream diff analysis tool classifies locally modified RPM packages by how their changes relate
to upstream Fedora. This helps the team systematically decide which modifications should be proposed
upstream, which are Hummingbird-specific, and which need more complex handling.

The primary interface is the `/upstream-diff` Claude Code skill, which orchestrates the full
workflow. The skill delegates all deterministic operations (diffing, metadata lookup, caching) to
`ci/upstream_diff.py` and applies its own judgment only for the classification step.

## Classification Categories

Each modified package is classified into one of five categories:

| Category               | Meaning                                                        | Action                                  |
| ---------------------- | -------------------------------------------------------------- | --------------------------------------- |
| `upstreamable`         | Changes can be submitted to Fedora as-is                       | File upstream PR                        |
| `hummingbird-specific` | Changes are intentional and specific to Hummingbird            | Keep locally, no upstream action needed |
| `complex`              | Changes need investigation or partial upstreaming              | Requires human decision                 |
| `mixed`                | Some changes are upstreamable, others are Hummingbird-specific | Split and handle separately             |
| `no-diff`              | Package is marked modified but has no actual diff              | Consider unmarking as modified          |

## Workflow

Invoke the skill in Claude Code with `/upstream-diff`. When called with no arguments, it offers
three modes:

### Analyze

Prepares the next batch of unanalyzed or stale packages, reviews each diff and any upstream PR
activity, then classifies each package into a category with reasoning and a recommended action.
Results are cached locally so subsequent runs skip already-analyzed packages.

You can also target specific packages by name (e.g., `/upstream-diff analyze bash glibc`).

### View Results

Displays the cached analysis as a summary table. Supports filtering by category and showing
details for a single package. Use this to get a quick overview of where things stand across all
modified packages.

### JIRA

Creates or updates a JIRA issue for a specific package to track the upstream proposal. The skill
generates a pre-filled description and analysis comment from the cached data, previews it for
confirmation, and then files or updates the issue. The JIRA issue key is recorded in the cache so
it appears in the view output.

## Script Reference

The skill uses `ci/upstream_diff.py` under the hood. The script can also be called directly for
automation, scripting, or when working outside Claude Code.

Note that `prepare` and `save` are two halves of a classification pipeline: `prepare` gathers raw
data (diffs, metadata, upstream PRs, category definitions), but its output requires human or LLM
judgment to select a category and write the reasoning fields before calling `save`. The skill's
**Analyze** mode bridges this gap automatically.

### Prepare

Gather metadata, diffs, upstream PR status, and the classification schema — this is the data the
skill's **Analyze** mode feeds into its classification step:

```bash
# Specific packages
./ci/upstream_diff.py prepare bash glibc

# Next batch of unanalyzed packages
./ci/upstream_diff.py prepare --batch 10
```

### Save

Store a classification result. The skill's **Analyze** mode calls this after classifying each
package:

```bash
./ci/upstream_diff.py save <package> \
  --category <category> \
  --changes-summary "One-line summary of changes" \
  --reasoning "Why this category was chosen" \
  --recommendation "Recommended next action" \
  --upstream-prs <pr_id1> <pr_id2> ...

# No related upstream PRs
./ci/upstream_diff.py save <package> \
  --category hummingbird-specific \
  --changes-summary "Custom Hummingbird macros for FIPS" \
  --reasoning "FIPS build flags are specific to Hummingbird" \
  --upstream-prs ""
```

The `--hummingbird-macros` flag can be added when changes use Hummingbird-specific RPM macros.

Results are cached in `.cache/upstream-diff-analysis.json`.

### View

Display cached results — this is what the skill's **View Results** mode calls:

```bash
# Summary table
./ci/upstream_diff.py view

# Single package detail
./ci/upstream_diff.py view <package>

# Filter by category
./ci/upstream_diff.py view --category upstreamable

# Include unanalyzed modified packages
./ci/upstream_diff.py view --all

# Markdown-formatted links (for JIRA or rendered contexts)
./ci/upstream_diff.py view --markdown

# Raw JSON
./ci/upstream_diff.py view --json
```

### Check Upstream PRs

Check whether local changes have already been submitted or merged in the upstream Fedora dist-git
repository:

```bash
./ci/upstream_diff.py check-prs <package1> <package2> ...
```

Shows open and recently merged (last 90 days) pull requests from the upstream Fedora dist-git repo.

### Update Related PRs

After initial analysis, update which upstream PRs are related to a package's local changes:

```bash
# Set related PRs
./ci/upstream_diff.py save --set-upstream-prs <package> <pr_id1> <pr_id2> ...

# Clear related PRs
./ci/upstream_diff.py save --set-upstream-prs <package>
```

### JIRA Templates

Generate pre-filled JIRA content from cached analysis — this is what the skill's **JIRA** mode
uses:

```bash
./ci/upstream_diff.py jira-template <package> [--epic HUM-1613]
```

Record a JIRA issue key after creating an issue:

```bash
./ci/upstream_diff.py save --set-jira <package> HUM-XXXX
```

## Related Documentation

- [Package Modification Tracking](package-modification-tracking.md) — how packages are marked
  modified and how diffs are computed
- [Rebuilding Packages](rebuilding-packages.md) — how to rebuild and backport patches
