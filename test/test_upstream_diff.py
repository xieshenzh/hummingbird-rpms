"""Tests for upstream_diff analysis tool."""

import json
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def upstream_diff_module(tmp_path):
    """Load upstream_diff.py as a module with paths redirected to tmp_path."""
    script_path = Path(__file__).parent.parent / 'ci' / 'upstream_diff.py'
    module = types.ModuleType('upstream_diff')
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)

    module.CACHE_DIR = tmp_path / '.cache'
    module.CACHE_FILE = tmp_path / '.cache' / 'upstream-diff-analysis.json'
    module.METADATA_DIR = tmp_path / 'metadata'

    module.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    module.METADATA_DIR.mkdir(parents=True, exist_ok=True)

    return module


@pytest.fixture
def sample_metadata(upstream_diff_module, tmp_path):
    """Create sample metadata files."""
    metadata_dir = tmp_path / 'metadata'
    packages = {
        'caddy': {
            'branch': 'rawhide',
            'modification_status': 'modified',
            'modification_reason': 'Weaken systemd dependency',
            'sha': 'abc123',
            'source': 'https://src.fedoraproject.org/rpms/caddy.git',
            'version': '2.9.0',
            'release': '1',
        },
        'automake': {
            'branch': 'rawhide',
            'modification_status': 'modified',
            'modification_reason': 'Exclude gcc-objc BuildRequires',
            'sha': 'def456',
            'source': 'https://src.fedoraproject.org/rpms/automake.git',
            'version': '1.18.1',
            'release': '4',
        },
        'curl': {
            'branch': 'rawhide',
            'modification_status': 'modified',
            'modification_reason': 'Test parallelism tuning',
            'sha': 'ghi789',
            'source': 'https://src.fedoraproject.org/rpms/curl.git',
            'version': '8.12.0',
            'release': '1',
        },
        'PyYAML': {
            'branch': 'rawhide',
            'modification_status': 'clean',
            'sha': 'jkl012',
            'source': 'https://src.fedoraproject.org/rpms/PyYAML.git',
            'version': '6.0.3',
            'release': '1',
        },
        'native-pkg': {
            'modification_status': 'native',
            'version': '1.0',
            'release': '1',
        },
    }
    for name, data in packages.items():
        (metadata_dir / f'{name}.json').write_text(json.dumps(data))
    return packages


@pytest.fixture
def sample_cache(upstream_diff_module, tmp_path):
    """Create a sample cache with one cached entry."""
    cache = {
        'packages': {
            'caddy': {
                'analyzed_at': '2025-01-01T00:00:00Z',
                'metadata_sha': 'abc123',
                'category': 'upstreamable',
                'changes_summary': 'Weakened systemd dep',
                'reasoning': 'Portable improvement',
                'recommendation': 'Submit Fedora PR',
                'hummingbird_macros': False,
                'jira_issue': 'HUM-1679',
                'fedora_bug': None,
            },
        },
    }
    (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text(json.dumps(cache))
    return cache


# --- Cache management tests ---

class TestLoadCache:
    def test_empty_file(self, upstream_diff_module, tmp_path):
        result = upstream_diff_module.load_cache()
        assert result == {'packages': {}}

    def test_valid_cache(self, upstream_diff_module, sample_cache):
        result = upstream_diff_module.load_cache()
        assert 'caddy' in result['packages']
        assert result['packages']['caddy']['category'] == 'upstreamable'

    def test_invalid_json(self, upstream_diff_module, tmp_path):
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text('not json')
        result = upstream_diff_module.load_cache()
        assert result == {'packages': {}}

    def test_missing_packages_key(self, upstream_diff_module, tmp_path):
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text('{"foo": "bar"}')
        result = upstream_diff_module.load_cache()
        assert result['packages'] == {}


class TestSaveCache:
    def test_creates_cache_dir(self, upstream_diff_module, tmp_path):
        import shutil
        shutil.rmtree(tmp_path / '.cache')
        upstream_diff_module.CACHE_DIR = tmp_path / '.cache'
        upstream_diff_module.CACHE_FILE = tmp_path / '.cache' / 'upstream-diff-analysis.json'
        cache = {'packages': {'test': {'category': 'complex'}}}
        upstream_diff_module.save_cache(cache)
        assert upstream_diff_module.CACHE_FILE.exists()

    def test_sets_last_updated(self, upstream_diff_module):
        cache = {'packages': {}}
        upstream_diff_module.save_cache(cache)
        saved = json.loads(upstream_diff_module.CACHE_FILE.read_text())
        assert 'last_updated' in saved

    def test_preserves_other_packages(self, upstream_diff_module, sample_cache):
        cache = upstream_diff_module.load_cache()
        cache['packages']['newpkg'] = {'category': 'complex'}
        upstream_diff_module.save_cache(cache)
        saved = json.loads(upstream_diff_module.CACHE_FILE.read_text())
        assert 'caddy' in saved['packages']
        assert 'newpkg' in saved['packages']


# --- Batch selection tests ---

class TestGetBatch:
    def test_pending_packages(self, upstream_diff_module, sample_metadata):
        cache = {'packages': {}}
        metadata = upstream_diff_module.get_all_modified_metadata()
        batch, info = upstream_diff_module.get_batch(metadata, cache, 10)
        assert 'caddy' in batch
        assert 'automake' in batch
        assert 'curl' in batch

    def test_skips_clean_and_native(self, upstream_diff_module, sample_metadata):
        metadata = upstream_diff_module.get_all_modified_metadata()
        assert 'PyYAML' not in metadata
        assert 'native-pkg' not in metadata

    def test_skips_cached_current(self, upstream_diff_module, sample_metadata, sample_cache):
        cache = upstream_diff_module.load_cache()
        metadata = upstream_diff_module.get_all_modified_metadata()
        batch, info = upstream_diff_module.get_batch(metadata, cache, 10)
        assert 'caddy' not in batch
        assert info['cached_count'] == 1

    def test_stale_before_pending(self, upstream_diff_module, sample_metadata):
        cache = {
            'packages': {
                'caddy': {'metadata_sha': 'OLD_SHA', 'category': 'upstreamable'},
            },
        }
        metadata = upstream_diff_module.get_all_modified_metadata()
        batch, info = upstream_diff_module.get_batch(metadata, cache, 10)
        assert batch[0] == 'caddy'
        assert info['batch_info']['caddy'] == 'stale'

    def test_respects_batch_size(self, upstream_diff_module, sample_metadata):
        cache = {'packages': {}}
        metadata = upstream_diff_module.get_all_modified_metadata()
        batch, info = upstream_diff_module.get_batch(metadata, cache, 1)
        assert len(batch) == 1


# --- Save validation tests ---

class TestCmdSave:
    def _make_args(self, **kwargs):
        import argparse
        defaults = {
            'package': 'caddy',
            'category': 'upstreamable',
            'changes_summary': 'Test summary',
            'reasoning': 'Test reasoning',
            'recommendation': 'Test recommendation',
            'hummingbird_macros': False,
            'jira_issue': None,
            'fedora_bug': None,
            'set_jira': None,
            'upstream_prs': [],
            'set_upstream_prs': None,
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_save_valid_category(self, upstream_diff_module, sample_metadata):
        for cat in upstream_diff_module.VALID_CATEGORIES:
            args = self._make_args(category=cat)
            with patch.object(upstream_diff_module, 'load_package_metadata',
                              return_value=sample_metadata['caddy']):
                upstream_diff_module.cmd_save(args)
            cache = upstream_diff_module.load_cache()
            assert cache['packages']['caddy']['category'] == cat

    def test_hummingbird_suffix_appended(self, upstream_diff_module, sample_metadata):
        args = self._make_args(
            package='automake',
            category='hummingbird-specific',
            recommendation='Cannot upstream as-is',
            hummingbird_macros=True,
        )
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['automake']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        rec = cache['packages']['automake']['recommendation']
        assert 'Revisit if Fedora packaging guidelines' in rec
        assert 'refactor to avoid the hummingbird macro' in rec

    def test_hummingbird_suffix_idempotent(self, upstream_diff_module, sample_metadata):
        suffix = upstream_diff_module.HUMMINGBIRD_RECOMMENDATION_SUFFIX.strip()
        args = self._make_args(
            package='automake',
            category='hummingbird-specific',
            recommendation=f'Cannot upstream as-is. {suffix}',
            hummingbird_macros=True,
        )
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['automake']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        rec = cache['packages']['automake']['recommendation']
        assert rec.count('Revisit if Fedora packaging guidelines') == 1

    def test_no_diff_recommendation(self, upstream_diff_module, sample_metadata):
        args = self._make_args(
            category='no-diff',
            recommendation='',
        )
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['recommendation'] == 'Consider unmarking as modified'

    def test_no_diff_recommendation_from_cli(self, upstream_diff_module, sample_metadata, capsys):
        """Verify the CLI accepts a no-diff save without an explicit --recommendation."""
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch('sys.argv', [
                'upstream_diff.py', 'save', 'caddy',
                '--category', 'no-diff',
                '--changes-summary', 'No diff found',
                '--reasoning', 'Empty diff',
                '--upstream-prs', '',
            ]):
                upstream_diff_module.main()
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['recommendation'] == 'Consider unmarking as modified'

    def test_preserves_jira_issue(self, upstream_diff_module, sample_metadata, sample_cache):
        args = self._make_args(jira_issue=None)
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['jira_issue'] == 'HUM-1679'

    def test_set_jira_updates_only_jira_field(self, upstream_diff_module, sample_cache):
        args = self._make_args(set_jira=['caddy', 'HUM-9999'])
        upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['jira_issue'] == 'HUM-9999'
        assert cache['packages']['caddy']['category'] == 'upstreamable'

    def test_set_jira_missing_package_errors(self, upstream_diff_module):
        args = self._make_args(set_jira=['nonexistent', 'HUM-1234'])
        with pytest.raises(SystemExit):
            upstream_diff_module.cmd_save(args)

    def test_save_missing_package_errors(self, upstream_diff_module):
        args = self._make_args(package='nonexistent')
        with patch.object(upstream_diff_module, 'load_package_metadata', return_value=None):
            with pytest.raises(SystemExit):
                upstream_diff_module.cmd_save(args)

    def test_save_with_related_prs(self, upstream_diff_module, sample_metadata):
        all_prs = [
            {'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
             'branch': 'rawhide', 'url': 'https://example.com/pr/4'},
            {'id': 5, 'title': 'Unrelated', 'status': 'Open', 'user': 'dev2',
             'branch': 'rawhide', 'url': 'https://example.com/pr/5'},
        ]
        args = self._make_args(upstream_prs=['4'])
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=all_prs):
                upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert len(cache['packages']['caddy']['upstream_prs']) == 1
        assert cache['packages']['caddy']['upstream_prs'][0]['id'] == 4

    def test_save_with_no_related_prs(self, upstream_diff_module, sample_metadata):
        args = self._make_args(upstream_prs=[''])
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['upstream_prs'] == []

    def test_save_with_empty_upstream_prs_list(self, upstream_diff_module, sample_metadata):
        args = self._make_args(upstream_prs=[])
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['upstream_prs'] == []

    def test_set_upstream_prs_updates_existing(self, upstream_diff_module, sample_cache,
                                               sample_metadata):
        all_prs = [
            {'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
             'branch': 'rawhide', 'url': 'https://example.com/pr/4'},
            {'id': 118, 'title': 'Move nologin', 'status': 'Merged', 'user': 'bsherman1',
             'branch': 'rawhide', 'url': 'https://example.com/pr/118'},
        ]
        args = self._make_args(set_upstream_prs=['caddy', '118'])
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=all_prs):
                upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['category'] == 'upstreamable'
        assert len(cache['packages']['caddy']['upstream_prs']) == 1
        assert cache['packages']['caddy']['upstream_prs'][0]['id'] == 118

    def test_set_upstream_prs_clears_prs(self, upstream_diff_module, sample_cache):
        args = self._make_args(set_upstream_prs=['caddy'])
        upstream_diff_module.cmd_save(args)
        cache = upstream_diff_module.load_cache()
        assert cache['packages']['caddy']['upstream_prs'] == []
        assert cache['packages']['caddy']['category'] == 'upstreamable'

    def test_set_upstream_prs_missing_package_errors(self, upstream_diff_module):
        args = self._make_args(set_upstream_prs=['nonexistent', '4'])
        with pytest.raises(SystemExit):
            upstream_diff_module.cmd_save(args)


# --- View output tests ---

class TestCmdView:
    def _make_args(self, json_output=False, category=None, all_flag=False, package=None, markdown=False):
        import argparse
        pkg = [package] if isinstance(package, str) else (package or [])
        return argparse.Namespace(json=json_output, category=category, package=pkg,
                                  markdown=markdown, **{'all': all_flag})

    def test_empty_cache(self, upstream_diff_module, capsys):
        upstream_diff_module.cmd_view(self._make_args())
        captured = capsys.readouterr()
        assert 'No packages analyzed' in captured.out

    def test_grouped_by_category(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args())
        captured = capsys.readouterr()
        assert '### Upstreamable (1)' in captured.out
        assert 'caddy' in captured.out

    def test_jira_plain_by_default(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args())
        captured = capsys.readouterr()
        assert 'HUM-1679' in captured.out
        assert '[HUM-1679](https://redhat.atlassian.net/browse/HUM-1679)' not in captured.out

    def test_jira_links_formatted_with_markdown(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args(markdown=True))
        captured = capsys.readouterr()
        assert '[HUM-1679](https://redhat.atlassian.net/browse/HUM-1679)' in captured.out

    def test_missing_jira_shows_dash(self, upstream_diff_module, tmp_path, capsys):
        cache = {
            'packages': {
                'testpkg': {
                    'category': 'complex',
                    'changes_summary': 'test',
                    'jira_issue': None,
                },
            },
        }
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text(json.dumps(cache))
        upstream_diff_module.cmd_view(self._make_args())
        captured = capsys.readouterr()
        assert '| — | — |' in captured.out

    def test_json_mode(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args(json_output=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert 'caddy' in data['packages']

    def test_category_counts(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args())
        captured = capsys.readouterr()
        assert 'Total cached: 1 packages' in captured.out
        assert 'upstreamable: 1' in captured.out

    def test_view_category_filter(self, upstream_diff_module, tmp_path, capsys):
        cache = {
            'packages': {
                'caddy': {
                    'category': 'upstreamable',
                    'changes_summary': 'Weakened systemd dep',
                    'jira_issue': None,
                },
                'automake': {
                    'category': 'hummingbird-specific',
                    'changes_summary': 'Gated gcc-objc',
                    'jira_issue': None,
                },
                'curl': {
                    'category': 'complex',
                    'changes_summary': 'Test tuning',
                    'jira_issue': None,
                },
            },
        }
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text(json.dumps(cache))
        upstream_diff_module.cmd_view(self._make_args(category='upstreamable'))
        captured = capsys.readouterr()
        assert '### Upstreamable (1)' in captured.out
        assert 'caddy' in captured.out
        assert '### Complex' not in captured.out
        assert '### Hummingbird' not in captured.out
        assert 'automake' not in captured.out
        assert 'curl' not in captured.out

    def test_view_all_shows_unanalyzed(self, upstream_diff_module, sample_metadata, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args(all_flag=True))
        captured = capsys.readouterr()
        assert '### Unanalyzed' in captured.out
        assert 'automake' in captured.out
        assert 'curl' in captured.out
        assert 'caddy' not in captured.out.split('### Unanalyzed')[1]

    def test_view_single_package(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args(package='caddy'))
        captured = capsys.readouterr()
        assert '**caddy**' in captured.out
        assert 'Category: upstreamable' in captured.out
        assert 'Reasoning: Portable improvement' in captured.out
        assert 'Recommendation: Submit Fedora PR' in captured.out
        assert 'JIRA: HUM-1679' in captured.out
        assert 'Related upstream PRs: —' in captured.out
        assert 'Metadata SHA: abc123' in captured.out

    def test_view_shows_related_prs(self, upstream_diff_module, tmp_path, capsys):
        cache = {
            'packages': {
                'caddy': {
                    'analyzed_at': '2025-01-01T00:00:00Z',
                    'metadata_sha': 'abc123',
                    'category': 'upstreamable',
                    'changes_summary': 'Test',
                    'reasoning': 'Test',
                    'recommendation': 'Test',
                    'hummingbird_macros': False,
                    'jira_issue': None,
                    'fedora_bug': None,
                    'upstream_prs': [
                        {'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                         'branch': 'rawhide', 'url': 'https://example.com/pr/4'},
                        {'id': 118, 'title': 'Move nologin', 'status': 'Merged', 'user': 'bsherman1',
                         'branch': 'rawhide', 'url': 'https://example.com/pr/118'},
                    ],
                },
            },
        }
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text(json.dumps(cache))
        upstream_diff_module.cmd_view(self._make_args(package='caddy'))
        captured = capsys.readouterr()
        assert 'Related upstream PRs:' in captured.out
        assert 'PR #4: Fix dep (Open)' in captured.out
        assert 'PR #118: Move nologin (Merged)' in captured.out

    def test_view_single_package_json(self, upstream_diff_module, sample_cache, capsys):
        upstream_diff_module.cmd_view(self._make_args(package='caddy', json_output=True))
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert 'caddy' in data
        assert data['caddy']['category'] == 'upstreamable'

    def test_view_single_package_not_found(self, upstream_diff_module, sample_cache):
        with pytest.raises(SystemExit):
            upstream_diff_module.cmd_view(self._make_args(package='nonexistent'))


# --- JIRA template tests ---

class TestCmdJiraTemplate:
    def _make_args(self, package='caddy', epic='HUM-1613'):
        import argparse
        return argparse.Namespace(package=package, epic=epic)

    def test_description_contains_reason_and_diff(self, upstream_diff_module, sample_cache, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output',
                              return_value='--- a/caddy.spec\n+++ b/caddy.spec\n-old\n+new'):
                upstream_diff_module.cmd_jira_template(self._make_args())
        captured = capsys.readouterr()
        assert '## Modification Reason' in captured.out
        assert 'Weaken systemd dependency' in captured.out
        assert '## Spec Diff' in captured.out
        assert '--- a/caddy.spec' in captured.out

    def test_comment_contains_analysis(self, upstream_diff_module, sample_cache, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff text'):
                upstream_diff_module.cmd_jira_template(self._make_args())
        captured = capsys.readouterr()
        assert '## Analysis' in captured.out
        assert '**Category:** upstreamable' in captured.out
        assert '**Recommendation:** Submit Fedora PR' in captured.out

    def test_custom_epic(self, upstream_diff_module, sample_cache, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
                upstream_diff_module.cmd_jira_template(self._make_args(epic='HUM-9999'))
        captured = capsys.readouterr()
        assert 'Parent: HUM-9999' in captured.out

    def test_jira_template_includes_pr_comment(self, upstream_diff_module, tmp_path,
                                               sample_metadata, capsys):
        cache = {
            'packages': {
                'caddy': {
                    'analyzed_at': '2025-01-01T00:00:00Z',
                    'metadata_sha': 'abc123',
                    'category': 'upstreamable',
                    'changes_summary': 'Test',
                    'reasoning': 'Test',
                    'recommendation': 'Test',
                    'hummingbird_macros': False,
                    'jira_issue': None,
                    'fedora_bug': None,
                    'upstream_prs': [
                        {'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                         'branch': 'rawhide', 'url': 'https://example.com/pr/4'},
                    ],
                },
            },
        }
        (tmp_path / '.cache' / 'upstream-diff-analysis.json').write_text(json.dumps(cache))
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
                upstream_diff_module.cmd_jira_template(self._make_args())
        captured = capsys.readouterr()
        assert '=== JIRA PR COMMENT ===' in captured.out
        assert '## Related Upstream PRs' in captured.out
        assert '[PR #4: Fix dep](https://example.com/pr/4) (by dev1, -> rawhide)' in captured.out
        assert '=== END PR COMMENT ===' in captured.out

    def test_jira_template_no_pr_comment_when_empty(self, upstream_diff_module,
                                                    sample_cache, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
                upstream_diff_module.cmd_jira_template(self._make_args())
        captured = capsys.readouterr()
        assert '=== JIRA PR COMMENT ===' not in captured.out

    def test_missing_package_errors(self, upstream_diff_module):
        with pytest.raises(SystemExit):
            upstream_diff_module.cmd_jira_template(self._make_args(package='nonexistent'))


# --- Prepare output tests ---

class TestCmdPrepare:
    def _make_args(self, packages=None, batch=10):
        import argparse
        return argparse.Namespace(packages=packages or [], batch=batch)

    def test_batch_mode(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff output'):
            upstream_diff_module.cmd_prepare(self._make_args(batch=2))
        captured = capsys.readouterr()
        assert '=== UPSTREAM DIFF ANALYSIS: PREPARE ===' in captured.out
        assert '--- PACKAGE:' in captured.out
        assert '=== END PREPARE ===' in captured.out

    def test_specific_packages(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff text'):
                upstream_diff_module.cmd_prepare(self._make_args(packages=['caddy']))
        captured = capsys.readouterr()
        assert '--- PACKAGE: caddy ---' in captured.out
        assert 'diff text' in captured.out

    def test_includes_schema(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
            upstream_diff_module.cmd_prepare(self._make_args(batch=1))
        captured = capsys.readouterr()
        assert '--- CLASSIFICATION SCHEMA ---' in captured.out
        assert 'category: upstreamable|hummingbird-specific|complex|mixed|no-diff' in captured.out

    def test_includes_category_definitions(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
            upstream_diff_module.cmd_prepare(self._make_args(batch=1))
        captured = capsys.readouterr()
        assert '--- CATEGORY DEFINITIONS ---' in captured.out
        assert 'Hummingbird-specific' in captured.out
        assert 'upstreamable' in captured.out.lower()

    def test_empty_diff_note(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value=''):
            upstream_diff_module.cmd_prepare(self._make_args(batch=1))
        captured = capsys.readouterr()
        assert 'no-diff' in captured.out

    def test_all_cached_message(self, upstream_diff_module, sample_metadata, sample_cache, tmp_path, capsys):
        cache = upstream_diff_module.load_cache()
        metadata = upstream_diff_module.get_all_modified_metadata()
        for pkg, data in metadata.items():
            cache['packages'][pkg] = {
                'metadata_sha': data['sha'],
                'category': 'complex',
            }
        upstream_diff_module.save_cache(cache)
        upstream_diff_module.cmd_prepare(self._make_args())
        captured = capsys.readouterr()
        assert 'already cached and up to date' in captured.out

    def test_nonexistent_package_errors(self, upstream_diff_module, sample_metadata):
        with patch.object(upstream_diff_module, 'load_package_metadata', return_value=None):
            with pytest.raises(SystemExit):
                upstream_diff_module.cmd_prepare(self._make_args(packages=['nonexistent']))

    def test_shows_modification_reason(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
            upstream_diff_module.cmd_prepare(self._make_args(batch=3))
        captured = capsys.readouterr()
        assert 'Weaken systemd dependency' in captured.out
        assert 'Exclude gcc-objc BuildRequires' in captured.out

    def test_prepare_includes_upstream_prs(self, upstream_diff_module, sample_metadata, capsys):
        mock_prs = [{'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                     'branch': 'rawhide',
                     'url': 'https://src.fedoraproject.org/rpms/caddy/pull-request/4'}]
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=mock_prs):
                upstream_diff_module.cmd_prepare(self._make_args(batch=1))
        captured = capsys.readouterr()
        assert 'UPSTREAM PRs:' in captured.out
        assert 'PR #4: Fix dep' in captured.out

    def test_prepare_no_upstream_prs(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'get_diff_output', return_value='diff'):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=[]):
                upstream_diff_module.cmd_prepare(self._make_args(batch=1))
        captured = capsys.readouterr()
        assert '(no open or recently merged PRs)' in captured.out


# --- Upstream PR tests ---

class TestGetUpstreamPrs:
    def test_success_open_and_merged(self, upstream_diff_module):
        import time
        recent_ts = str(time.time() - 3600)  # 1 hour ago
        open_prs = [{'id': 4, 'title': 'Drop sysusers_create_compat',
                     'status': 'Open', 'user': {'name': 'zbyszek'}, 'branch': 'rawhide'}]
        merged_prs = [{'id': 118, 'title': 'Move nologin requirement',
                       'status': 'Merged', 'user': {'name': 'bsherman1'},
                       'branch': 'rawhide', 'closed_at': recent_ts}]
        with patch.object(upstream_diff_module, '_fetch_pagure_prs',
                          side_effect=[open_prs, merged_prs]):
            prs = upstream_diff_module.get_upstream_prs(
                'caddy', 'https://src.fedoraproject.org/rpms/caddy.git')
        assert len(prs) == 2
        assert prs[0]['id'] == 4
        assert prs[0]['status'] == 'Open'
        assert prs[0]['user'] == 'zbyszek'
        assert '/pull-request/4' in prs[0]['url']
        assert prs[1]['id'] == 118
        assert prs[1]['status'] == 'Merged'
        assert prs[1]['user'] == 'bsherman1'

    def test_filters_old_merged(self, upstream_diff_module):
        old_ts = str(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
        merged_prs = [{'id': 50, 'title': 'Old fix', 'status': 'Merged',
                       'user': {'name': 'dev1'}, 'branch': 'rawhide',
                       'closed_at': old_ts}]
        with patch.object(upstream_diff_module, '_fetch_pagure_prs',
                          side_effect=[[], merged_prs]):
            prs = upstream_diff_module.get_upstream_prs(
                'caddy', 'https://src.fedoraproject.org/rpms/caddy.git')
        assert prs == []

    def test_network_error(self, upstream_diff_module):
        with patch.object(upstream_diff_module, '_fetch_pagure_prs',
                          return_value=None):
            prs = upstream_diff_module.get_upstream_prs(
                'caddy', 'https://src.fedoraproject.org/rpms/caddy.git')
        assert prs == []

    def test_non_fedora_source(self, upstream_diff_module):
        prs = upstream_diff_module.get_upstream_prs(
            'mypkg', 'https://gitlab.com/some/repo.git')
        assert prs == []

    def test_invalid_json(self, upstream_diff_module):
        with patch.object(upstream_diff_module, '_fetch_pagure_prs',
                          return_value=None):
            prs = upstream_diff_module.get_upstream_prs(
                'caddy', 'https://src.fedoraproject.org/rpms/caddy.git')
        assert prs == []


class TestCmdCheckPrs:
    def _make_args(self, packages):
        import argparse
        return argparse.Namespace(packages=packages)

    def test_shows_open_prs(self, upstream_diff_module, sample_metadata, capsys):
        mock_prs = [{'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                     'branch': 'rawhide',
                     'url': 'https://src.fedoraproject.org/rpms/caddy/pull-request/4'}]
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=mock_prs):
                upstream_diff_module.cmd_check_prs(self._make_args(['caddy']))
        captured = capsys.readouterr()
        assert '=== UPSTREAM PRs: caddy ===' in captured.out
        assert 'Open:' in captured.out
        assert 'PR #4: Fix dep (by dev1, -> rawhide)' in captured.out

    def test_no_open_prs(self, upstream_diff_module, sample_metadata, capsys):
        with patch.object(upstream_diff_module, 'load_package_metadata',
                          return_value=sample_metadata['caddy']):
            with patch.object(upstream_diff_module, 'get_upstream_prs', return_value=[]):
                upstream_diff_module.cmd_check_prs(self._make_args(['caddy']))
        captured = capsys.readouterr()
        assert '(no open or recently merged PRs)' in captured.out

    def test_missing_package_errors(self, upstream_diff_module):
        with patch.object(upstream_diff_module, 'load_package_metadata', return_value=None):
            with pytest.raises(SystemExit):
                upstream_diff_module.cmd_check_prs(self._make_args(['nonexistent']))


class TestFormatUpstreamPrs:
    def test_empty(self, upstream_diff_module):
        assert upstream_diff_module.format_upstream_prs([]) == '(no open or recently merged PRs)'

    def test_open_only(self, upstream_diff_module):
        prs = [{'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                'branch': 'rawhide', 'url': 'https://example.com/pr/4'}]
        result = upstream_diff_module.format_upstream_prs(prs)
        assert 'Open:' in result
        assert 'PR #4: Fix dep (by dev1, -> rawhide)' in result
        assert 'Recently merged:' not in result

    def test_merged_only(self, upstream_diff_module):
        prs = [{'id': 118, 'title': 'Move nologin', 'status': 'Merged', 'user': 'bsherman1',
                'branch': 'rawhide', 'url': 'https://example.com/pr/118'}]
        result = upstream_diff_module.format_upstream_prs(prs)
        assert 'Recently merged:' in result
        assert 'PR #118: Move nologin (by bsherman1, -> rawhide)' in result
        assert 'Open:' not in result

    def test_both_groups(self, upstream_diff_module):
        prs = [
            {'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
             'branch': 'rawhide', 'url': 'https://example.com/pr/4'},
            {'id': 118, 'title': 'Move nologin', 'status': 'Merged', 'user': 'bsherman1',
             'branch': 'rawhide', 'url': 'https://example.com/pr/118'},
        ]
        result = upstream_diff_module.format_upstream_prs(prs)
        assert 'Open:' in result
        assert 'Recently merged:' in result
        open_pos = result.index('Open:')
        merged_pos = result.index('Recently merged:')
        assert open_pos < merged_pos

    def test_markdown_open(self, upstream_diff_module):
        prs = [{'id': 13, 'title': 'Make systemd optional', 'status': 'Open', 'user': 'dev1',
                'branch': 'rawhide', 'url': 'https://example.com/pr/13'}]
        result = upstream_diff_module.format_upstream_prs(prs, markdown=True)
        assert '- [PR #13: Make systemd optional](https://example.com/pr/13) (by dev1, -> rawhide)' in result
        assert 'Open:' in result

    def test_markdown_merged(self, upstream_diff_module):
        prs = [{'id': 118, 'title': 'Move nologin', 'status': 'Merged', 'user': 'bsherman1',
                'branch': 'rawhide', 'url': 'https://example.com/pr/118'}]
        result = upstream_diff_module.format_upstream_prs(prs, markdown=True)
        assert '- [PR #118: Move nologin](https://example.com/pr/118) (by bsherman1, -> rawhide)' in result
        assert 'Recently merged:' in result

    def test_markdown_no_plain_url_line(self, upstream_diff_module):
        prs = [{'id': 4, 'title': 'Fix dep', 'status': 'Open', 'user': 'dev1',
                'branch': 'rawhide', 'url': 'https://example.com/pr/4'}]
        result = upstream_diff_module.format_upstream_prs(prs, markdown=True)
        assert '    https://example.com/pr/4' not in result


