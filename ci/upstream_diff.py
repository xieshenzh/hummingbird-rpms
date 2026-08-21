#!/usr/bin/env python3
"""
Upstream diff analysis tool for agent skills.

This script separates deterministic operations (gathering data, saving
results, generating templates) from LLM classification judgment. It is
designed to be called from AI agent skills (Claude Code, Goose, Cursor,
etc.) — not directly by humans.

Workflow:
  1. prepare  — gather metadata, diffs, and classification schema
  2. (agent classifies each package using LLM judgment)
  3. save     — validate classification and write to cache
  4. view     — display cached results
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from dist_git import (
    METADATA_DIR,
    ROOT_DIR,
    diff_package,
    load_package_metadata,
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

CACHE_DIR = ROOT_DIR / '.cache'
CACHE_FILE = CACHE_DIR / 'upstream-diff-analysis.json'

VALID_CATEGORIES = ('upstreamable', 'hummingbird-specific', 'complex', 'mixed', 'no-diff')

JIRA_BASE_URL = 'https://redhat.atlassian.net/browse/'

HUMMINGBIRD_RECOMMENDATION_SUFFIX = (
    ' Revisit if Fedora packaging guidelines for derivative '
    'macros change, or refactor to avoid the hummingbird macro.'
)

CATEGORY_DEFINITIONS = """\
### Category 1: Hummingbird-specific — do NOT upstream

Changes gated on hummingbird macros. Look for: `%{defined hummingbird}`,
`%{?hummingbird}`, `0%{?hummingbird:1}`, `0%{?hummingbird}`. Even if the
underlying change is valuable, the macro form cannot currently go upstream
as-is. Set hummingbird_macros: true.

Examples: `automake` (gcc-objc gated on `%{?hummingbird}`), `openssl`
(FIPS patches gated on `%{defined hummingbird}`).

### Category 2: Clean & upstreamable — recommend

Self-contained, portable improvements following Fedora guidelines.
Indicators: weakening systemd `Requires:` to `Recommends:`, path
portability fixes, `%bcond` toggles for optional features, spec bug
fixes. Good for a Fedora dist-git PR.

Examples: `caddy` (removes systemd runtime dep for containers).

### Category 3: Complex / arbitrary — do NOT recommend

Environment-specific, will converge naturally, or too complex.
Indicators: test tuning, version bumps ahead of Fedora, disabling
features for broken buildroot deps, large-scale removal, circular
dependency workarounds, pre-existing changes with no clear reason.

Examples: `curl` (test parallelism tuning), `gcc` (disable language
frontends), `buildah` (circular dep workaround).

### Mixed-category packages

If changes span categories, list each separately. Use category: "mixed".
Note which parts could be upstreamed independently.

### No-diff packages

Package marked modified but diff is empty. Use category: "no-diff".
This is distinct from "clean" (a metadata state meaning the package
was never modified). A no-diff package should be unmarked as modified.\
"""


def load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if 'packages' not in data:
            data['packages'] = {}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {'packages': {}}


def save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache['last_updated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    tmp_fd, tmp_path = tempfile.mkstemp(dir=CACHE_DIR, suffix='.json')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(cache, f, indent=2)
            f.write('\n')
        os.rename(tmp_path, CACHE_FILE)
    except BaseException:
        os.unlink(tmp_path)
        raise


def get_all_modified_metadata() -> dict[str, dict]:
    result = {}
    for f in sorted(METADATA_DIR.glob('*.json')):
        pkg = f.stem
        with open(f) as fh:
            data = json.load(fh)
        if data.get('modification_status') == 'modified':
            result[pkg] = data
    return result


def get_batch(metadata: dict[str, dict], cache: dict,
              batch_size: int) -> tuple[list[str], dict[str, Any]]:
    stale = []
    pending = []
    cached_count = 0
    batch_info = {}

    for pkg, data in metadata.items():
        sha = data.get('sha', '')
        if pkg in cache.get('packages', {}):
            if cache['packages'][pkg].get('metadata_sha') == sha:
                cached_count += 1
                continue
            else:
                stale.append(pkg)
                batch_info[pkg] = 'stale'
        else:
            pending.append(pkg)
            batch_info[pkg] = 'new'

    batch = (stale + pending)[:batch_size]
    total = cached_count + len(stale) + len(pending)
    info = {
        'batch': [p for p in batch],
        'batch_info': {p: batch_info[p] for p in batch},
        'cached_count': cached_count,
        'stale_count': len(stale),
        'pending_count': len(pending),
        'total_modified': total,
    }
    return batch, info


def get_diff_output(package_name: str) -> str | None:
    result = diff_package(package_name, output_mode='full', capture=True)
    if result is None:
        return None
    assert isinstance(result, str), f"Unexpected non-string from diff_package: {result!r}"
    return result


PAGURE_API_BASE = 'https://src.fedoraproject.org/api/0/rpms'
PAGURE_WEB_BASE = 'https://src.fedoraproject.org/rpms'


MERGED_PR_MAX_AGE_DAYS = 90


def _fetch_pagure_prs(package_name: str, status: str) -> list[dict] | None:
    """Fetch PRs from Pagure API. Returns None on failure."""
    url = f'{PAGURE_API_BASE}/{package_name}/pull-requests?status={status}'
    if status == 'Merged':
        url += '&per_page=10'
    try:
        output = subprocess.check_output(
            ['curl', '--silent', '--show-error', '--fail', '--retry', '3', '--max-time', '10', url],
            text=True, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(output).get('requests', [])
    except json.JSONDecodeError:
        return None


def get_upstream_prs(package_name: str, source_url: str) -> list[dict]:
    """Fetch open and recently merged PRs from upstream Fedora dist-git.

    Returns a list of PR dicts with keys: id, title, status, user, branch, url.
    Merged PRs are limited to those closed within the last 90 days.
    Returns [] on any failure (network, non-Fedora source, bad JSON).
    """
    if 'src.fedoraproject.org' not in source_url:
        return []

    prs = []
    cutoff = datetime.now(timezone.utc).timestamp() - (MERGED_PR_MAX_AGE_DAYS * 86400)

    for status in ('Open', 'Merged'):
        raw = _fetch_pagure_prs(package_name, status)
        if raw is None:
            logging.warning('Failed to fetch %s PRs for %s', status.lower(), package_name)
            continue
        for pr in raw:
            if status == 'Merged':
                closed_at = pr.get('closed_at')
                if closed_at is None or float(closed_at) < cutoff:
                    continue
            prs.append({
                'id': pr['id'],
                'title': pr['title'],
                'status': pr.get('status', status),
                'user': pr.get('user', {}).get('name', '?'),
                'branch': pr.get('branch', '?'),
                'url': f'{PAGURE_WEB_BASE}/{package_name}/pull-request/{pr["id"]}',
            })
    return prs


def format_upstream_prs(prs: list[dict], *, markdown: bool = False) -> str:
    if not prs:
        return '(no open or recently merged PRs)'
    open_prs = [p for p in prs if p.get('status') == 'Open']
    merged_prs = [p for p in prs if p.get('status') == 'Merged']
    lines = []
    for label, group in [('Open:', open_prs), ('Recently merged:', merged_prs)]:
        if not group:
            continue
        lines.append(label)
        for pr in group:
            if markdown:
                lines.append(
                    f'- [PR #{pr["id"]}: {pr["title"]}]({pr["url"]})'
                    f' (by {pr["user"]}, -> {pr["branch"]})'
                )
            else:
                lines.append(f'  PR #{pr["id"]}: {pr["title"]} (by {pr["user"]}, -> {pr["branch"]})')
                lines.append(f'    {pr["url"]}')
    return '\n'.join(lines)


# --- Subcommands ---

def cmd_prepare(args: argparse.Namespace) -> None:
    cache = load_cache()
    metadata = get_all_modified_metadata()

    if args.packages:
        batch = args.packages
        batch_info = {}
        for pkg in batch:
            if pkg not in metadata:
                md = load_package_metadata(pkg)
                if not md:
                    print(f'ERROR: Package {pkg} not found', file=sys.stderr)
                    sys.exit(1)
                if md.get('modification_status') != 'modified':
                    print(f'ERROR: Package {pkg} is not modified (status: {md.get("modification_status")})',
                          file=sys.stderr)
                    sys.exit(1)
                metadata[pkg] = dict(md)
            sha = metadata[pkg].get('sha', '')
            if pkg in cache.get('packages', {}) and cache['packages'][pkg].get('metadata_sha') == sha:
                batch_info[pkg] = 'cached (forced re-analysis)'
            elif pkg in cache.get('packages', {}):
                batch_info[pkg] = 'stale'
            else:
                batch_info[pkg] = 'new'
        info: dict[str, Any] = {
            'batch_info': batch_info,
            'cached_count': sum(1 for p, m in metadata.items()
                                if p in cache.get('packages', {})
                                and cache['packages'][p].get('metadata_sha') == m.get('sha', '')),
            'total_modified': len(metadata),
        }
    else:
        batch, info = get_batch(metadata, cache, args.batch)
        if not batch:
            print('All modified packages are already cached and up to date.')
            print(f'Progress: {info["cached_count"]}/{info["total_modified"]} cached')
            return

    bi: dict[str, str] = info['batch_info']
    stale = sum(1 for s in bi.values() if s == 'stale')
    new = sum(1 for s in bi.values() if s == 'new')
    forced = sum(1 for s in bi.values() if 'forced' in s)
    parts = [f'{stale} stale', f'{new} new']
    if forced:
        parts.append(f'{forced} forced re-analysis')
    print('=== UPSTREAM DIFF ANALYSIS: PREPARE ===')
    print(f'Batch: {len(batch)} packages ({", ".join(parts)})')
    print(f'Progress: {info["cached_count"]}/{info["total_modified"]} cached')
    print()
    print('--- CLASSIFICATION SCHEMA ---')
    print('For each package, provide ALL of the following fields:')
    print('  category: upstreamable|hummingbird-specific|complex|mixed|no-diff')
    print('  hummingbird_macros: true|false')
    print('  changes_summary: <one-line summary of what changed>')
    print('  reasoning: <why this category>')
    print('  recommendation: <what to do next>')
    print()
    print('--- CATEGORY DEFINITIONS ---')
    print(CATEGORY_DEFINITIONS)
    print()

    for pkg in batch:
        data = metadata[pkg]
        print(f'--- PACKAGE: {pkg} ---')
        print(f'Source: {data.get("source", "N/A")}')
        print(f'Branch: {data.get("branch", "N/A")}')
        print(f'SHA: {data.get("sha", "N/A")}')
        print(f'Modification reason: {data.get("modification_reason", "N/A")}')
        print(f'Cache status: {bi.get(pkg, "unknown")}')
        print()

        diff_text = get_diff_output(pkg)
        if diff_text is None:
            print('DIFF: (independent package — no upstream)')
        elif diff_text == '':
            print('DIFF: (empty — no differences found)')
            print('NOTE: This package has no diff. Classify as "no-diff".')
        else:
            print('DIFF:')
            print(diff_text)
        print()

        source = data.get('source', '')
        if source:
            prs = get_upstream_prs(pkg, source)
            print('UPSTREAM PRs:')
            print(format_upstream_prs(prs))
            print()

    print('=== END PREPARE ===')


def _resolve_upstream_prs(package: str, pr_ids: list[int]) -> list[dict]:
    """Fetch full PR metadata from upstream and filter to the given IDs."""
    md = load_package_metadata(package)
    source = md.get('source', '') if md else ''
    if not source:
        return []
    all_prs = get_upstream_prs(package, source)
    found_ids = {p['id'] for p in all_prs}
    missing = [pid for pid in pr_ids if pid not in found_ids]
    if missing:
        logging.warning(
            'PR IDs not found in open/recently-merged window for %s: %s '
            '(PRs older than %d days are excluded)',
            package, missing, MERGED_PR_MAX_AGE_DAYS,
        )
    return [p for p in all_prs if p['id'] in pr_ids]


def cmd_save(args: argparse.Namespace) -> None:
    if args.set_jira:
        pkg, key = args.set_jira
        cache = load_cache()
        if pkg not in cache.get('packages', {}):
            print(f'ERROR: Package {pkg} not found in cache. Analyze it first.', file=sys.stderr)
            sys.exit(1)
        cache['packages'][pkg]['jira_issue'] = key
        save_cache(cache)
        print(f'Updated: {pkg} -> jira_issue={key}', file=sys.stderr)
        return

    if args.set_upstream_prs:
        pkg = args.set_upstream_prs[0]
        cache = load_cache()
        if pkg not in cache.get('packages', {}):
            print(f'ERROR: Package {pkg} not found in cache. Analyze it first.', file=sys.stderr)
            sys.exit(1)
        pr_ids = [int(x) for x in args.set_upstream_prs[1:]]
        if pr_ids:
            related = _resolve_upstream_prs(pkg, pr_ids)
        else:
            related = []
        cache['packages'][pkg]['upstream_prs'] = related
        save_cache(cache)
        print(f'Updated: {pkg} -> upstream_prs={[p["id"] for p in related]}', file=sys.stderr)
        return

    package = args.package
    md = load_package_metadata(package)
    if not md:
        print(f'ERROR: Package {package} not found in metadata', file=sys.stderr)
        sys.exit(1)

    sha = md.get('sha', '')
    recommendation = args.recommendation

    if args.category == 'hummingbird-specific' and args.hummingbird_macros:
        if HUMMINGBIRD_RECOMMENDATION_SUFFIX.strip() not in recommendation:
            recommendation = recommendation.rstrip('.') + '.' + HUMMINGBIRD_RECOMMENDATION_SUFFIX

    if args.category == 'no-diff' and not recommendation:
        recommendation = 'Consider unmarking as modified'

    pr_ids = []
    for val in (args.upstream_prs or []):
        val = val.strip()
        if val:
            pr_ids.append(int(val))
    if pr_ids:
        upstream_prs = _resolve_upstream_prs(package, pr_ids)
    else:
        upstream_prs = []

    entry = {
        'analyzed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'metadata_sha': sha,
        'category': args.category,
        'changes_summary': args.changes_summary,
        'reasoning': args.reasoning,
        'recommendation': recommendation,
        'hummingbird_macros': args.hummingbird_macros,
        'jira_issue': args.jira_issue,
        'fedora_bug': args.fedora_bug,
        'upstream_prs': upstream_prs,
    }

    cache = load_cache()
    existing = cache['packages'].get(package, {})
    if entry['jira_issue'] is None:
        entry['jira_issue'] = existing.get('jira_issue')
    if entry['fedora_bug'] is None:
        entry['fedora_bug'] = existing.get('fedora_bug')

    cache['packages'][package] = entry
    save_cache(cache)
    print(f'Saved: {package} -> {args.category}', file=sys.stderr)


def _format_jira_link(jira: str | None, markdown: bool) -> str:
    if not jira:
        return '—'
    if markdown:
        return f'[{jira}]({JIRA_BASE_URL}{jira})'
    return jira


def cmd_view(args: argparse.Namespace) -> None:
    cache = load_cache()
    packages = cache.get('packages', {})
    md = getattr(args, 'markdown', False)

    if len(args.package) == 1:
        pkg_name = args.package[0]
        entry = packages.get(pkg_name)
        if not entry:
            print(f'Package {pkg_name} not found in cache.', file=sys.stderr)
            sys.exit(1)
        if args.json:
            json.dump({pkg_name: entry}, sys.stdout, indent=2)
            print()
            return
        jira = entry.get('jira_issue')
        jira_str = _format_jira_link(jira, md)
        print(f'**{pkg_name}**')
        print(f'- Category: {entry.get("category", "N/A")}')
        print(f'- Summary: {entry.get("changes_summary", "N/A")}')
        print(f'- Reasoning: {entry.get("reasoning", "N/A")}')
        print(f'- Recommendation: {entry.get("recommendation", "N/A")}')
        print(f'- Hummingbird macros: {entry.get("hummingbird_macros", False)}')
        print(f'- JIRA: {jira_str}')
        print(f'- Fedora bug: {entry.get("fedora_bug") or "—"}')
        cached_prs = entry.get('upstream_prs', [])
        if cached_prs:
            print('- Related upstream PRs:')
            for pr in cached_prs:
                if md:
                    print(f'  - [PR #{pr["id"]}: {pr["title"]}]({pr["url"]}) ({pr.get("status", "?")})')
                else:
                    print(f'  - PR #{pr["id"]}: {pr["title"]} ({pr.get("status", "?")}) — {pr["url"]}')
        else:
            print('- Related upstream PRs: —')
        print(f'- Analyzed: {entry.get("analyzed_at", "N/A")}')
        print(f'- Metadata SHA: {entry.get("metadata_sha", "N/A")}')
        return

    if len(args.package) > 1:
        missing = [p for p in args.package if p not in packages]
        if missing:
            print(f'Packages not found in cache: {", ".join(missing)}', file=sys.stderr)
            sys.exit(1)
        if args.json:
            json.dump({p: packages[p] for p in args.package}, sys.stdout, indent=2)
            print()
            return
        print(f'**Showing {len(args.package)} packages**')
        print('| Package | Category | JIRA | PRs | Summary |')
        print('|---------|----------|------|-----|---------|')
        for pkg_name in sorted(args.package):
            entry = packages[pkg_name]
            jira = entry.get('jira_issue')
            jira_str = _format_jira_link(jira, md)
            pr_count = len(entry.get('upstream_prs', []))
            pr_str = str(pr_count) if pr_count else '—'
            summary = entry.get('changes_summary', '')
            print(f'| {pkg_name} | {entry.get("category", "?")} | {jira_str} | {pr_str} | {summary} |')
        return

    if args.json:
        json.dump(cache, sys.stdout, indent=2)
        print()
        return

    if not packages and not getattr(args, 'all', False):
        print('No packages analyzed yet.')
        return

    groups: dict[str, list[tuple[str, dict]]] = {
        'upstreamable': [],
        'mixed': [],
        'hummingbird-specific': [],
        'complex': [],
        'no-diff': [],
    }
    for pkg, entry in sorted(packages.items()):
        cat = entry.get('category', 'complex')
        if cat in groups:
            groups[cat].append((pkg, entry))
        else:
            groups['complex'].append((pkg, entry))

    if args.category:
        groups = {args.category: groups.get(args.category, [])}

    total = len(packages)
    counts = ' | '.join(f'{cat}: {len(pkgs)}' for cat, pkgs in groups.items())
    print(f'**Total cached: {total} packages**')
    print(counts)

    category_labels = {
        'upstreamable': 'Upstreamable',
        'mixed': 'Mixed',
        'hummingbird-specific': 'Hummingbird-specific',
        'complex': 'Complex',
        'no-diff': 'No-diff',
    }
    for cat, pkgs in groups.items():
        label = category_labels.get(cat, cat)
        print(f'\n### {label} ({len(pkgs)})')
        if not pkgs:
            print('(none)')
            continue
        print('| Package | JIRA | PRs | Summary |')
        print('|---------|------|-----|---------|')
        for pkg, entry in pkgs:
            jira = entry.get('jira_issue')
            jira_str = _format_jira_link(jira, md)
            pr_count = len(entry.get('upstream_prs', []))
            pr_str = str(pr_count) if pr_count else '—'
            summary = entry.get('changes_summary', '')
            print(f'| {pkg} | {jira_str} | {pr_str} | {summary} |')

    if getattr(args, 'all', False):
        all_modified = get_all_modified_metadata()
        unanalyzed = sorted(p for p in all_modified if p not in packages)
        print(f'\n### Unanalyzed ({len(unanalyzed)})')
        if not unanalyzed:
            print('(none)')
        else:
            for pkg in unanalyzed:
                reason = all_modified[pkg].get('modification_reason', '')
                print(f'- {pkg}: {reason}' if reason else f'- {pkg}')


def cmd_jira_template(args: argparse.Namespace) -> None:
    cache = load_cache()
    packages = cache.get('packages', {})
    package = args.package

    if package not in packages:
        print(f'ERROR: Package {package} not found in cache. Analyze it first.', file=sys.stderr)
        sys.exit(1)

    entry = packages[package]
    md = load_package_metadata(package)
    modification_reason = md.get('modification_reason', 'N/A') if md else 'N/A'

    diff_text = get_diff_output(package)
    if diff_text is None:
        diff_text = '(independent package — no upstream)'
    elif diff_text == '':
        diff_text = '(no differences found)'

    print('=== JIRA DESCRIPTION ===')
    print(f'Summary: Consider upstream spec changes for {package}')
    print('Type: Story')
    print(f'Parent: {args.epic}')
    print()
    print('Body:')
    print('## Modification Reason')
    print()
    print(modification_reason)
    print()
    print('## Spec Diff')
    print()
    print('```')
    print(diff_text)
    print('```')
    print('=== END DESCRIPTION ===')
    print()
    print('=== JIRA COMMENT ===')
    print('## Analysis')
    print()
    print(f'**Category:** {entry.get("category", "N/A")}')
    print(f'**Reasoning:** {entry.get("reasoning", "N/A")}')
    print(f'**Recommendation:** {entry.get("recommendation", "N/A")}')
    print('=== END COMMENT ===')

    cached_prs = entry.get('upstream_prs', [])
    if cached_prs:
        print()
        print('=== JIRA PR COMMENT ===')
        print('## Related Upstream PRs')
        print()
        print(format_upstream_prs(cached_prs, markdown=True))
        print('=== END PR COMMENT ===')


def cmd_check_prs(args: argparse.Namespace) -> None:
    for pkg in args.packages:
        md = load_package_metadata(pkg)
        if not md:
            print(f'ERROR: Package {pkg} not found', file=sys.stderr)
            sys.exit(1)
        source = md.get('source', '')
        prs = get_upstream_prs(pkg, source)
        print(f'=== UPSTREAM PRs: {pkg} ===')
        print(format_upstream_prs(prs))
        print()



def main() -> None:
    parser = argparse.ArgumentParser(
        description='Upstream diff analysis tool for agent skills',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
This script is designed to be called from AI agent skills
(Claude Code, Goose, Cursor, etc.). It separates deterministic
operations from LLM classification judgment.

Usage from agent skills:

  # Gather data for LLM classification
  ./ci/upstream_diff.py prepare <package1> <package2>
  ./ci/upstream_diff.py prepare --batch 10

  # Save LLM classification results (--upstream-prs required)
  ./ci/upstream_diff.py save <package> --category upstreamable \\
    --changes-summary "..." --reasoning "..." --recommendation "..." \\
    --upstream-prs 4 13

  # Save with no related PRs
  ./ci/upstream_diff.py save <package> --category complex \\
    --changes-summary "..." --reasoning "..." --upstream-prs ""

  # Update JIRA issue key on existing entry
  ./ci/upstream_diff.py save --set-jira <package> HUM-XXXX

  # Update related upstream PRs on existing entry
  ./ci/upstream_diff.py save --set-upstream-prs <package> 118 13

  # View cached results
  ./ci/upstream_diff.py view

  # Generate pre-filled JIRA templates
  ./ci/upstream_diff.py jira-template <package>

  # Check for open and recently merged upstream PRs
  ./ci/upstream_diff.py check-prs <package1> <package2>
""",
    )

    subparsers = parser.add_subparsers(dest='command')

    # prepare
    prep = subparsers.add_parser('prepare',
                                 help='Gather metadata, diffs, and classification schema')
    prep.add_argument('packages', nargs='*',
                      help='Package names to analyze (omit for --batch mode)')
    prep.add_argument('--batch', type=int, default=10,
                      help='Number of unanalyzed packages to prepare (default: 10)')

    # save
    save = subparsers.add_parser('save',
                                 help='Save classification results to cache')
    save.add_argument('package', nargs='?',
                      help='Package name')
    save.add_argument('--category', choices=VALID_CATEGORIES,
                      help='Classification category')
    save.add_argument('--changes-summary',
                      help='One-line summary of changes')
    save.add_argument('--reasoning',
                      help='Why this category was chosen')
    save.add_argument('--recommendation',
                      help='Recommended action')
    save.add_argument('--hummingbird-macros', action='store_true',
                      help='Changes use hummingbird macros')
    save.add_argument('--jira-issue',
                      help='JIRA issue key (e.g., HUM-1234)')
    save.add_argument('--fedora-bug',
                      help='Fedora bug URL')
    save.add_argument('--upstream-prs', nargs='*', metavar='PR_ID',
                      help='Related upstream PR IDs (or "" for none)')
    save.add_argument('--set-jira', nargs=2, metavar=('PACKAGE', 'KEY'),
                      help='Update only the JIRA issue key on an existing entry')
    save.add_argument('--set-upstream-prs', nargs='+', metavar='PKG_AND_IDS',
                      help='Update only upstream PRs on an existing entry: --set-upstream-prs PACKAGE [PR_ID ...]')

    # view
    view = subparsers.add_parser('view',
                                 help='Display cached analysis results')
    view.add_argument('package', nargs='*',
                      help='Show details for one package, or filtered table for multiple')
    view.add_argument('--json', action='store_true',
                      help='Output raw JSON instead of formatted tables')
    view.add_argument('--category', choices=VALID_CATEGORIES,
                      help='Show only packages in this category')
    view.add_argument('--all', action='store_true',
                      help='Include unanalyzed modified packages')
    view.add_argument('--markdown', action='store_true',
                      help='Format links as Markdown (for JIRA/rendered output)')

    # jira-template
    jira = subparsers.add_parser('jira-template',
                                 help='Generate pre-filled JIRA description and comment')
    jira.add_argument('package',
                      help='Package name (must be analyzed)')
    jira.add_argument('--epic', default='HUM-1613',
                      help='Parent epic key (default: HUM-1613)')

    # check-prs
    check = subparsers.add_parser('check-prs',
                                   help='Check for open upstream PRs in Fedora dist-git')
    check.add_argument('packages', nargs='+',
                       help='Package names to check')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Validate save subcommand arguments
    if args.command == 'save' and not args.set_jira and not args.set_upstream_prs:
        if not args.package:
            parser.error('save requires a package name (or --set-jira / --set-upstream-prs)')
        for field in ('category', 'changes_summary', 'reasoning'):
            if not getattr(args, field.replace('-', '_'), None):
                parser.error(f'save requires --{field.replace("_", "-")}')
        if args.recommendation is None:
            args.recommendation = ''
        if args.upstream_prs is None:
            parser.error('save requires --upstream-prs (pass "" for no related PRs)')

    match args.command:
        case 'prepare':
            cmd_prepare(args)
        case 'save':
            cmd_save(args)
        case 'view':
            cmd_view(args)
        case 'jira-template':
            cmd_jira_template(args)
        case 'check-prs':
            cmd_check_prs(args)


if __name__ == '__main__':
    main()
