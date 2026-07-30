"""Tests for check_nevr_conflicts.

Where practical these tests exercise the real `rpmspec`/`rpm` binaries against
synthetic spec files (rather than mocking them), since the whole point of the
tool is to match what those real tools would produce for an actual build.
Only `dnf`/Pulp network access is faked, via a PATH-injected script.
"""

import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

# A minimal but structurally faithful stand-in for
# rpms/hummingbird-release/hummingbird-release.spec: it installs the same
# macros.dist heredoc (dist, distcore, hummingbird, fedora) that the real
# mock buildroot has installed at build time.
HUMMINGBIRD_RELEASE_SPEC_FIXTURE = """\
Name:           hummingbird-release
Version:        1
Release:        1%{?dist}
Summary:        Test release files
License:        MIT
BuildArch:      noarch

%description
Test

%install
mkdir -p %{buildroot}%{_rpmmacrodir}
cat > %{buildroot}%{_rpmmacrodir}/macros.dist << EOF
%%distcore            .hum1
%%dist                %%{!?distprefix0:%%{?distprefix}}%%{expand:%%{lua:for i=0,9999 do print("%%{?distprefix" .. i .."}") end}}%%{distcore}%%{?with_bootstrap:%%{__bootstrap}}%%{?buildrelease:+build%%{buildrelease}}
%%hummingbird         1
%%fedora              44
EOF

%files
"""

FAKE_DNF_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

call_log = os.environ.get("FAKE_DNF_CALL_LOG")
if call_log:
    with open(call_log, "a") as f:
        f.write("call\\n")

data = json.loads(os.environ["FAKE_DNF_DATA"])
args = sys.argv[1:]

base_url = None
for i, arg in enumerate(args):
    if arg == "--repofrompath":
        base_url = args[i + 1].split(",", 1)[1]
        break

names = []
if "repoquery" in args:
    rest = args[args.index("repoquery") + 1:]
    if "--qf" in rest:
        qf_index = rest.index("--qf")
        names = rest[:qf_index] + rest[qf_index + 2:]
    else:
        names = rest

repo_data = data.get(base_url or "", {})
for name in names:
    for evr in repo_data.get(name, []):
        print(name + "\\t" + evr)
"""

FAKE_RPM_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

data = json.loads(os.environ["FAKE_RPM_DATA"])
path = sys.argv[-1]
entry = data.get(path)
if entry is None:
    sys.exit(1)
print(entry["name"] + "\\t" + entry["evr"] + "\\t" + entry["arch"])
"""

PUBLIC_BASE_URLS = {
    "x86_64": "https://pulp.example/x86_64/",
    "aarch64": "https://pulp.example/aarch64/",
    "source": "https://pulp.example/source/",
}


#
# Fixtures
#

@pytest.fixture
def checker(tmp_path: Path):
    """Load check_nevr_conflicts.py with its module-level paths pointed at tmp_path."""
    (tmp_path / 'rpms' / 'hummingbird-release').mkdir(parents=True)
    (tmp_path / 'rpms' / 'hummingbird-release' / 'hummingbird-release.spec').write_text(
        HUMMINGBIRD_RELEASE_SPEC_FIXTURE
    )
    (tmp_path / 'ci').mkdir(exist_ok=True)
    (tmp_path / 'ci' / 'package-overrides.yaml').write_text('{}\n')

    script_path = Path(__file__).parent.parent / 'ci' / 'check_nevr_conflicts.py'
    module = types.ModuleType('check_nevr_conflicts')
    module.__file__ = str(script_path)
    code = compile(script_path.read_text(), str(script_path), 'exec')
    exec(code, module.__dict__)

    module.ROOT_DIR = tmp_path  # type: ignore[attr-defined]
    module.RPMS_DIR = tmp_path / 'rpms'  # type: ignore[attr-defined]
    module.PACKAGE_OVERRIDES_YAML = tmp_path / 'ci' / 'package-overrides.yaml'  # type: ignore[attr-defined]
    module.HUMMINGBIRD_RELEASE_SPEC = (  # type: ignore[attr-defined]
        tmp_path / 'rpms' / 'hummingbird-release' / 'hummingbird-release.spec'
    )
    return module


@pytest.fixture
def fake_dnf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fake `dnf` on PATH serving canned repoquery results.

    Returns a setter: set_data({base_url: {name: [evr, ...]}}).
    """
    bin_dir = tmp_path / 'fakebin-dnf'
    bin_dir.mkdir(exist_ok=True)
    dnf_path = bin_dir / 'dnf'
    dnf_path.write_text(FAKE_DNF_SCRIPT)
    dnf_path.chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}:{os.environ['PATH']}")

    def set_data(data: dict) -> None:
        monkeypatch.setenv('FAKE_DNF_DATA', json.dumps(data))

    set_data({})
    return set_data


@pytest.fixture
def fake_rpm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fake `rpm` on PATH serving canned `-qp --qf` results.

    Returns a setter: set_data({rpm_file_path: {"name":, "evr":, "arch":}}).
    """
    bin_dir = tmp_path / 'fakebin-rpm'
    bin_dir.mkdir(exist_ok=True)
    rpm_path = bin_dir / 'rpm'
    rpm_path.write_text(FAKE_RPM_SCRIPT)
    rpm_path.chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}:{os.environ['PATH']}")

    def set_data(data: dict) -> None:
        monkeypatch.setenv('FAKE_RPM_DATA', json.dumps(data))

    set_data({})
    return set_data


def _create_package(root: Path, name: str, spec_body: str) -> None:
    pkg_dir = root / 'rpms' / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / f'{name}.spec').write_text(spec_body)


def _rpmmacros_home(checker):
    return checker.make_rpmmacros_home(checker.get_distro_macros())


#
# get_distro_macros / make_rpmmacros_home
#

def test_get_distro_macros_extracts_real_dist_tag(checker):
    macros = checker.get_distro_macros()
    assert '%distcore            .hum1' in macros
    assert '%hummingbird         1' in macros
    assert '%fedora              44' in macros


def test_get_distro_macros_missing_spec_raises(checker):
    checker.HUMMINGBIRD_RELEASE_SPEC.unlink()
    with pytest.raises(checker.NevrCheckError):
        checker.get_distro_macros()


#
# Spec mode: NEVR prediction
#

def test_spec_candidates_applies_real_dist_tag(checker, tmp_path):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 3%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('widget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    evrs = {(c.name, c.arch): c.evr for c in candidates}
    assert evrs[('widget', 'x86_64')] == '1.0-3.hum1'
    assert evrs[('widget', 'aarch64')] == '1.0-3.hum1'
    assert evrs[('widget', 'src')] == '1.0-3.hum1'


def test_spec_candidates_handles_dist_embedded_in_custom_release_macro(checker, tmp_path):
    """Regression test: some packages (krb5, kernel-headers) embed %{?dist}
    inside a custom Release macro rather than as a plain trailing suffix.
    Naively nulling `dist` and re-appending a suffix (the initial approach)
    silently produces the wrong prediction for exactly this pattern; letting
    rpmspec expand the real macros.dist macros natively fixes it."""
    _create_package(tmp_path, 'krb5widget', """\
%global krb5_release 4%{?dist}
Name: krb5widget
Version: 2.0
Release: %{krb5_release}
Summary: Test
License: MIT

%description
Test

%files
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('krb5widget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    evrs = {(c.name, c.arch): c.evr for c in candidates}
    assert evrs[('krb5widget', 'x86_64')] == '2.0-4.hum1'


def test_spec_candidates_epoch_formatting(checker, tmp_path):
    _create_package(tmp_path, 'epochwidget', """\
Name: epochwidget
Epoch: 5
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('epochwidget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    evr = next(c.evr for c in candidates if c.name == 'epochwidget' and c.arch == 'x86_64')
    assert evr == '5:1.0-1.hum1'


def test_spec_candidates_no_epoch_has_no_none_literal(checker, tmp_path):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('widget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    for c in candidates:
        assert '(none)' not in c.evr


def test_spec_candidates_arch_specific_subpackage(checker, tmp_path):
    _create_package(tmp_path, 'archwidget', """\
Name: archwidget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%ifarch x86_64
%package extra
Summary: extra bits
%description extra
extra bits
%endif

%files

%ifarch x86_64
%files extra
%endif
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('archwidget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    by_name_arch = {(c.name, c.arch) for c in candidates}
    assert ('archwidget-extra', 'x86_64') in by_name_arch
    assert ('archwidget-extra', 'aarch64') not in by_name_arch
    assert ('archwidget', 'x86_64') in by_name_arch
    assert ('archwidget', 'aarch64') in by_name_arch


def test_spec_candidates_excludes_debuginfo_subpackage(checker, tmp_path):
    _create_package(tmp_path, 'dbgwidget', """\
Name: dbgwidget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%package debuginfo
Summary: debuginfo
%description debuginfo
debuginfo

%files

%files debuginfo
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('dbgwidget', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    names = {c.name for c in candidates}
    assert 'dbgwidget-debuginfo' not in names
    assert 'dbgwidget' in names


def test_spec_candidates_srpm_name_can_differ_from_directory_name(checker, tmp_path):
    """The SRPM/package Name: can differ from the rpms/<dir> name; the true
    name (from rpmspec --srpm) must be used for the Pulp lookup, not the
    directory name."""
    _create_package(tmp_path, 'python-foo', """\
Name: foo
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    home = _rpmmacros_home(checker)
    try:
        candidates = checker.get_spec_candidates('python-foo', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)

    names = {c.name for c in candidates}
    assert 'foo' in names
    assert 'python-foo' not in names
    # Candidate.package still reflects the rpms/<dir> name, for reporting.
    assert all(c.package == 'python-foo' for c in candidates)


def test_get_spec_candidates_requires_exactly_one_spec_file(checker, tmp_path):
    (tmp_path / 'rpms' / 'nospec').mkdir(parents=True)
    home = _rpmmacros_home(checker)
    try:
        with pytest.raises(checker.NevrCheckError):
            checker.get_spec_candidates('nospec', home)
    finally:
        shutil.rmtree(home, ignore_errors=True)


#
# Built-RPM mode
#

def test_get_built_rpm_candidates_reads_ground_truth_and_skips_debuginfo(checker, tmp_path, fake_rpm):
    rpms_dir = tmp_path / 'builds' / 'widget' / 'RPMS' / 'x86_64'
    rpms_dir.mkdir(parents=True)
    rpm_file = rpms_dir / 'widget-3.hum1.x86_64.rpm'
    rpm_file.write_text('fake')
    dbg_file = rpms_dir / 'widget-debuginfo-3.hum1.x86_64.rpm'
    dbg_file.write_text('fake')

    fake_rpm({
        str(rpm_file): {"name": "widget", "evr": "3.hum1", "arch": "x86_64"},
        str(dbg_file): {"name": "widget-debuginfo", "evr": "3.hum1", "arch": "x86_64"},
    })

    candidates = checker.get_built_rpm_candidates(tmp_path / 'builds' / 'widget')
    assert len(candidates) == 1
    assert candidates[0].name == 'widget'
    assert candidates[0].evr == '3.hum1'
    assert candidates[0].arch == 'x86_64'
    assert candidates[0].package == 'widget'


def test_get_built_rpm_candidates_no_rpms_raises(checker, tmp_path):
    empty_dir = tmp_path / 'builds' / 'empty'
    empty_dir.mkdir(parents=True)
    with pytest.raises(checker.NevrCheckError):
        checker.get_built_rpm_candidates(empty_dir)


def test_get_built_rpm_candidates_missing_dir_raises(checker, tmp_path):
    with pytest.raises(checker.NevrCheckError):
        checker.get_built_rpm_candidates(tmp_path / 'does-not-exist')


#
# Pulp comparison
#

def test_check_candidates_detects_conflict(checker, fake_dnf):
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget': ['3.hum1']}})
    candidates = [checker.Candidate('widget', 'widget', '3.hum1', 'x86_64')]

    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert len(results) == 1
    assert results[0].status == 'CONFLICT'
    assert 'x86_64' in results[0].note


def test_check_candidates_no_conflict_for_new_version(checker, fake_dnf):
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget': ['2.hum1']}})
    candidates = [checker.Candidate('widget', 'widget', '3.hum1', 'x86_64')]

    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert results[0].status == 'OK'


def test_check_candidates_brand_new_package_is_ok(checker, fake_dnf):
    fake_dnf({})
    candidates = [checker.Candidate('widget', 'widget', '1.hum1', 'x86_64')]

    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert results[0].status == 'OK'


def test_check_candidates_noarch_checked_against_both_arch_repos(checker, fake_dnf):
    fake_dnf({PUBLIC_BASE_URLS['aarch64']: {'widget-doc': ['1.hum1']}})
    candidates = [checker.Candidate('widget', 'widget-doc', '1.hum1', 'noarch')]

    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert results[0].status == 'CONFLICT'
    assert 'aarch64' in results[0].note


def test_check_candidates_src_checked_against_source_repo(checker, fake_dnf):
    fake_dnf({PUBLIC_BASE_URLS['source']: {'widget': ['1.hum1']}})
    candidates = [checker.Candidate('widget', 'widget', '1.hum1', 'src')]

    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert results[0].status == 'CONFLICT'
    assert 'source' in results[0].note


def test_check_candidates_batches_one_query_per_repo(checker, fake_dnf, tmp_path, monkeypatch):
    """Multiple candidate names in the same repo should be a single dnf call."""
    call_log = tmp_path / 'dnf_calls.log'
    monkeypatch.setenv('FAKE_DNF_CALL_LOG', str(call_log))
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget-a': ['1.hum1'], 'widget-b': ['1.hum1']}})

    candidates = [
        checker.Candidate('a', 'widget-a', '1.hum1', 'x86_64'),
        checker.Candidate('b', 'widget-b', '1.hum1', 'x86_64'),
    ]
    results = checker.check_candidates(candidates, PUBLIC_BASE_URLS, timeout=5)

    assert all(r.status == 'CONFLICT' for r in results)
    # Only the x86_64 repo has names to check (aarch64/source are empty and
    # short-circuited without invoking dnf at all).
    assert call_log.read_text().count('call') == 1


#
# private_product handling
#

def test_load_private_products(checker, tmp_path):
    (tmp_path / 'ci' / 'package-overrides.yaml').write_text(yaml.dump({
        'ldap-widget': {'private_product': 'ldap', 'timeout_hours': 8},
        'public-widget': {'timeout_hours': 4},
    }))

    assert checker.load_private_products() == {'ldap-widget': 'ldap'}


def test_load_private_products_missing_file_returns_empty(checker, tmp_path):
    (tmp_path / 'ci' / 'package-overrides.yaml').unlink()
    assert checker.load_private_products() == {}


#
# Package selection helpers
#

def test_has_spec_and_get_all_packages(checker, tmp_path):
    _create_package(tmp_path, 'widget', "Name: widget\nVersion: 1\nRelease: 1%{?dist}\n"
                                        "Summary: t\nLicense: MIT\n\n%description\nt\n\n%files\n")
    (tmp_path / 'rpms' / 'no-spec-dir').mkdir()

    assert checker.has_spec('widget') is True
    assert checker.has_spec('no-spec-dir') is False
    assert checker.has_spec('does-not-exist') is False

    all_packages = checker.get_all_packages()
    assert 'widget' in all_packages
    assert 'hummingbird-release' in all_packages
    assert 'no-spec-dir' not in all_packages


#
# End-to-end main()
#

def _argv_with_urls(*extra: str) -> list[str]:
    return [
        'check_nevr_conflicts.py', *extra,
        '--base-url', PUBLIC_BASE_URLS['x86_64'],
        '--base-url-aarch64', PUBLIC_BASE_URLS['aarch64'],
        '--base-url-source', PUBLIC_BASE_URLS['source'],
    ]


def test_main_exits_1_on_conflict(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 3%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget': ['1.0-3.hum1']}})
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('widget'))

    exit_code = checker.main()

    assert exit_code == 1
    assert 'CONFLICT' in capsys.readouterr().out


def test_main_spec_mode_shows_phantom_ok_note_when_mixed_results(
    checker, tmp_path, fake_dnf, monkeypatch, capsys,
):
    """When a spec-mode run has at least one OK result alongside a CONFLICT,
    print a reminder that OK doesn't diminish the CONFLICT finding (some OK
    results can be phantom candidates for a binary that's never actually
    produced -- see get_spec_candidates/_spec_binaries)."""
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 3%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    # Conflict only on x86_64; aarch64 and the SRPM come back OK.
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget': ['1.0-3.hum1']}})
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('widget'))

    exit_code = checker.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert 'Conflicts: 1' in out
    assert 'Note: in spec mode' in out
    assert 'never overrides or reduces the significance of any CONFLICT' in out


def test_main_spec_mode_no_note_when_all_conflict(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    """No OK results at all -> nothing to caveat, so the note is omitted."""
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 3%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    fake_dnf({
        PUBLIC_BASE_URLS['x86_64']: {'widget': ['1.0-3.hum1']},
        PUBLIC_BASE_URLS['aarch64']: {'widget': ['1.0-3.hum1']},
        PUBLIC_BASE_URLS['source']: {'widget': ['1.0-3.hum1']},
    })
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('widget'))

    exit_code = checker.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert 'Conflicts: 3' in out
    assert 'Note: in spec mode' not in out


def test_main_built_rpm_mode_never_shows_phantom_ok_note(
    checker, tmp_path, fake_dnf, fake_rpm, monkeypatch, capsys,
):
    """Built-RPM mode reads real files, so there's no phantom-candidate risk
    and the note should never be printed there, even with OK results."""
    rpms_dir = tmp_path / 'builds' / 'widget' / 'RPMS' / 'x86_64'
    rpms_dir.mkdir(parents=True)
    rpm_file = rpms_dir / 'widget-1.0-3.hum1.x86_64.rpm'
    rpm_file.write_text('fake')
    fake_rpm({str(rpm_file): {"name": "widget", "evr": "1.0-3.hum1", "arch": "x86_64"}})
    fake_dnf({})
    monkeypatch.setattr(sys, 'argv', [
        'check_nevr_conflicts.py', '--rpms-dir', str(tmp_path / 'builds' / 'widget'),
        '--base-url', PUBLIC_BASE_URLS['x86_64'],
        '--base-url-aarch64', PUBLIC_BASE_URLS['aarch64'],
        '--base-url-source', PUBLIC_BASE_URLS['source'],
    ])

    exit_code = checker.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert 'Conflicts: 0' in out
    assert 'Note: in spec mode' not in out


def test_main_exits_0_when_clean(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 3%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    fake_dnf({})
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('widget'))

    exit_code = checker.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert not any(line.startswith('CONFLICT') for line in out.splitlines())
    assert 'Conflicts: 0' in out


def test_main_json_output(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    fake_dnf({PUBLIC_BASE_URLS['x86_64']: {'widget': ['1.0-1.hum1']}})
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('widget', '--json'))

    exit_code = checker.main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    statuses = {r['name']: r['status'] for r in payload['results']}
    assert statuses['widget'] == 'CONFLICT'


def test_main_private_product_note(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    _create_package(tmp_path, 'ldap-widget', """\
Name: ldap-widget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    (tmp_path / 'ci' / 'package-overrides.yaml').write_text(
        yaml.dump({'ldap-widget': {'private_product': 'ldap'}})
    )
    fake_dnf({})
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('ldap-widget'))

    exit_code = checker.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert 'private_product' in out
    assert 'ldap' in out


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_get_changed_rpm_packages_excludes_metadata_only_changes(checker, tmp_path, monkeypatch):
    """A metadata/<pkg>.json-only change (e.g. `dist_git.py mark-modified`)
    must NOT be treated as a changed package: Konflux's actual build
    trigger only reacts to rpms/<pkg>/*** path changes, so predicting a
    NEVR for a package that isn't actually being rebuilt would produce a
    false-positive CONFLICT against its already-published NEVR."""
    repo = tmp_path
    _git(['init', '--initial-branch=main'], repo)
    _git(['config', 'user.name', 'Test'], repo)
    _git(['config', 'user.email', 'test@example.com'], repo)

    (repo / 'rpms' / 'existing').mkdir(parents=True)
    (repo / 'rpms' / 'existing' / 'existing.spec').write_text('placeholder\n')
    (repo / 'metadata').mkdir()
    (repo / 'metadata' / 'existing.json').write_text('{}\n')
    _git(['add', '.'], repo)
    _git(['commit', '-m', 'Initial commit'], repo)

    base_sha = _git(['rev-parse', 'HEAD'], repo).stdout.strip()
    _git(['update-ref', 'refs/remotes/origin/main', base_sha], repo)

    # Metadata-only change for 'metadata-only-pkg' -- no rpms/ directory at all.
    (repo / 'metadata' / 'metadata-only-pkg.json').write_text('{}\n')
    # A real spec change for 'spec-changed-pkg'.
    (repo / 'rpms' / 'spec-changed-pkg').mkdir()
    (repo / 'rpms' / 'spec-changed-pkg' / 'spec-changed-pkg.spec').write_text('placeholder\n')
    _git(['add', '.'], repo)
    _git(['commit', '-m', 'Metadata-only + spec change'], repo)

    monkeypatch.setattr(checker, 'ROOT_DIR', repo)
    monkeypatch.delenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', raising=False)

    result = checker.get_changed_rpm_packages()

    assert result == ['spec-changed-pkg']


def test_get_changed_rpm_packages_git_failure_raises(checker, tmp_path, monkeypatch):
    monkeypatch.setattr(checker, 'ROOT_DIR', tmp_path)
    monkeypatch.setenv('CI_MERGE_REQUEST_TARGET_BRANCH_NAME', 'main')
    # tmp_path is not a git repo at all, so the diff must fail cleanly.
    with pytest.raises(checker.NevrCheckError):
        checker.get_changed_rpm_packages()


def test_main_mr_mode_filters_to_packages_with_specs(checker, tmp_path, fake_dnf, monkeypatch, capsys):
    _create_package(tmp_path, 'widget', """\
Name: widget
Version: 1.0
Release: 1%{?dist}
Summary: Test
License: MIT

%description
Test

%files
""")
    fake_dnf({})
    # 'ghost-package' has no rpms/ directory (e.g. a metadata-only change for
    # a package that was since removed) and must be silently skipped rather
    # than crashing the whole run.
    monkeypatch.setattr(checker, 'get_changed_rpm_packages', lambda: ['widget', 'ghost-package'])
    monkeypatch.setattr(sys, 'argv', _argv_with_urls('--mr'))

    exit_code = checker.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert 'widget' in out
    assert 'ghost-package' not in out


def test_main_mr_mode_no_changes_is_clean(checker, monkeypatch, capsys):
    monkeypatch.setattr(checker, 'get_changed_rpm_packages', lambda: [])
    monkeypatch.setattr(sys, 'argv', ['check_nevr_conflicts.py', '--mr'])

    exit_code = checker.main()

    assert exit_code == 0
    assert 'No packages' in capsys.readouterr().out
