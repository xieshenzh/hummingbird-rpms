"""Unit tests for .cursor/skills/cve/cve_helper.py probe helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
HELPER_PATH = ROOT / ".cursor" / "skills" / "cve" / "cve_helper.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("cve_helper", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cve_helper"] = module
    spec.loader.exec_module(module)
    return module


helper = _load_helper()

# lib modules are importable after helper is loaded (it adds the skill dir to sys.path)
import lib.jira as lib_jira  # noqa: E402
import lib.models as lib_models  # noqa: E402
import lib.sbom as lib_sbom  # noqa: E402
import lib.spec as lib_spec  # noqa: E402
import lib.utils as lib_utils  # noqa: E402
import lib.vcs as lib_vcs  # noqa: E402


def test_normalize_argv_inserts_show_for_bare_tickets() -> None:
    assert helper.normalize_argv(["HUM-1234", "--title-only"]) == [
        "show",
        "HUM-1234",
        "--title-only",
    ]
    assert helper.normalize_argv(["bot-mrs", "boost"]) == ["bot-mrs", "boost"]


def test_pulp_sbom_package_dir_replaces_dots() -> None:
    assert lib_sbom.pulp_sbom_package_dir("grafana13.1") == "grafana13-1-main"
    assert lib_sbom.pulp_sbom_package_dir("boost") == "boost-main"


def test_parse_glab_mr_list() -> None:
    output = "\n".join(
        [
            "!3905  grafana13.1: Update axios (renovate) (renovate/axios)",
            "No open merge requests match your search",
            "!12  plain title",
        ]
    )
    entries = lib_vcs._parse_glab_mr_list(output, "open")
    assert [e.iid for e in entries] == ["3905", "12"]
    assert entries[0].web_url.endswith("/merge_requests/3905")
    assert "axios" in entries[0].title


def test_search_sbom_file_structured_hits(tmp_path: Path) -> None:
    sbom = {
        "components": [
            {
                "type": "library",
                "name": "brace-expansion",
                "version": "2.0.1",
                "purl": "pkg:npm/brace-expansion@2.0.1",
                "scope": "required",
            },
            {
                "type": "library",
                "name": "unrelated",
                "version": "1.0.0",
            },
        ]
    }
    path = tmp_path / "pkg.sbom.json"
    path.write_text(json.dumps(sbom), encoding="utf-8")
    hits = helper.search_sbom_file(path, ["brace-expansion"])
    assert len(hits) == 1
    assert hits[0].name == "brace-expansion"
    assert hits[0].version == "2.0.1"
    assert hits[0].scope == "required"


def test_search_sbom_file_text_fallback(tmp_path: Path) -> None:
    path = tmp_path / "plain.sbom.json"
    path.write_text('not-json but mentions EvilLib somewhere', encoding="utf-8")
    hits = helper.search_sbom_file(path, ["EvilLib"])
    assert hits
    assert hits[0].path == "text"
    assert "EvilLib" in hits[0].snippet


def test_probe_spec_deps_bundled_and_component(tmp_path: Path, monkeypatch) -> None:
    pkg = "fakepkg"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{pkg}.spec").write_text(
        "\n".join(
            [
                "Name: fakepkg",
                "Provides: bundled(golang.org/x/text) = 0.21.0",
                "Source0: https://example.com/fakepkg.tar.gz",
                "# mentions golang.org/x/text again",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    hits = helper.probe_spec_deps(pkg, "golang.org/x/text")
    kinds = {h.kind for h in hits}
    assert "bundled_provides" in kinds
    bundled = next(h for h in hits if h.kind == "bundled_provides")
    assert bundled.bundled_name == "golang.org/x/text"
    assert bundled.bundled_version == "0.21.0"


def test_run_rhjira_retries_transient_errors(monkeypatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[int] = []

    def fake_run(args, *, cwd=None):
        calls.append(args)
        if len(calls) < 3:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="proxy tunnel 403 Forbidden",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(lib_jira, "run_command", fake_run)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    result = lib_jira.run_rhjira(["show", "HUM-1"])
    assert result.returncode == 0
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_run_rhjira_does_not_retry_non_transient(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, *, cwd=None):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="issue not found")

    monkeypatch.setattr(lib_jira, "run_command", fake_run)
    result = lib_jira.run_rhjira(["show", "HUM-1"])
    assert result.returncode == 1
    assert len(calls) == 1


def test_read_local_spec_nvr(tmp_path: Path, monkeypatch) -> None:
    pkg = "mypkg"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{pkg}.spec").write_text(
        "\n".join([
            "%global mainver 2.5.1",
            "Name: mypkg",
            "Version: %{?ver_override}%{!?ver_override:%{mainver}}",
            "Release: 3%{?dist}",
            "",
            "%description",
            "A package.",
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    assert lib_spec.read_local_spec_nvr(pkg) == "mypkg-2.5.1-3"
    assert lib_spec.read_local_spec_nvr("nonexistent") == ""


def test_search_cve_in_spec_and_patches(tmp_path: Path, monkeypatch) -> None:
    pkg = "testpkg"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{pkg}.spec").write_text("Name: testpkg\nVersion: 1.0\n", encoding="utf-8")
    (pkg_dir / "fix-cve.patch").write_text(
        "# Backport for CVE-2024-12345\n--- a/foo.c\n+++ b/foo.c\n", encoding="utf-8"
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    assert lib_spec.search_cve_in_spec_and_patches(pkg, ["CVE-2024-12345"]) is True
    assert lib_spec.search_cve_in_spec_and_patches(pkg, ["CVE-2099-99999"]) is False
    assert lib_spec.search_cve_in_spec_and_patches("ghost", ["CVE-2024-12345"]) is False


def test_build_suggested_chat_title() -> None:
    reports = [
        lib_models.TicketReport(
            "HUM-1",
            "CVE-2026-12345 pkg: x",
            "New",
            "Bug",
            "me",
            "pscomponent:pkg",
        )
    ]
    assert helper.build_suggested_chat_title(reports) == "HUM-1 pkg"


def test_parse_agent_env_strips_quotes() -> None:
    import cve_analysis_bridge as bridge

    parsed = bridge.parse_agent_env(
        "\n".join(
            [
                "export JIRA_TOKEN='tok'",
                'export JIRA_EMAIL="user@redhat.com"',
                "export JIRA_SERVER=https://redhat.atlassian.net",
                "# comment",
                "not an assignment",
            ]
        )
    )
    assert parsed["JIRA_TOKEN"] == "tok"
    assert parsed["JIRA_EMAIL"] == "user@redhat.com"
    assert parsed["JIRA_SERVER"] == "https://redhat.atlassian.net"
    assert "not" not in parsed


def test_load_jira_auth_maps_rhjira_server_and_email(tmp_path: Path, monkeypatch) -> None:
    import cve_analysis_bridge as bridge

    agent = tmp_path / "agent.env"
    agent.write_text(
        "\n".join(
            [
                "export JIRA_TOKEN=from-file",
                "export JIRA_EMAIL=prarit@redhat.com",
                "export JIRA_SERVER=https://redhat.atlassian.net",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_SERVER", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    auth = bridge.load_jira_auth(environ={}, agent_env_path=agent)
    assert auth.token == "from-file"
    assert auth.base_url == "https://redhat.atlassian.net"
    assert auth.basic_auth_user == "prarit@redhat.com"
    assert str(agent) in auth.source


def test_load_jira_auth_prefers_process_env(tmp_path: Path) -> None:
    import cve_analysis_bridge as bridge

    agent = tmp_path / "agent.env"
    agent.write_text("export JIRA_TOKEN=file-token\n", encoding="utf-8")
    auth = bridge.load_jira_auth(
        environ={
            "JIRA_TOKEN": "env-token",
            "JIRA_URL": "https://example.invalid",
        },
        agent_env_path=agent,
    )
    assert auth.token == "env-token"
    assert auth.base_url == "https://example.invalid"
    assert auth.source == "env"


def test_cmd_deps_reports_missing_analysis(monkeypatch) -> None:
    import cve_analysis_bridge as bridge

    # cmd_deps is in cve_helper and uses load_jira_auth/jira_client_module
    # from its own namespace (imported directly from cve_analysis_bridge)
    monkeypatch.setattr(
        helper,
        "load_jira_auth",
        lambda: bridge.JiraAuth(
            token="x",
            base_url="https://redhat.atlassian.net",
            basic_auth_user="a@b.c",
            source="env",
        ),
    )

    def _boom():
        raise bridge.AnalysisImportError("not installed")

    monkeypatch.setattr(helper, "jira_client_module", _boom)
    rc = helper.cmd_deps(SimpleNamespace(json=False))
    assert rc == 1


def _fake_auth():
    return SimpleNamespace(
        token="tok",
        base_url="https://redhat.atlassian.net",
        basic_auth_user="user@redhat.com",
    )


def test_pulp_has_nvr_uses_listing_index(monkeypatch) -> None:
    index = SimpleNamespace(
        nvrs=["grafana13.1-13.1.1-1"],
        srpm_filenames={"grafana13.1-13.1.1-1.src.rpm"},
    )
    pulp = SimpleNamespace(
        fetch_srpm_listing_index=lambda pkg: index,
        fetch_hummingbird_latest_srpm=lambda pkg: "grafana13.1-13.1.0-1.src.rpm",
    )
    monkeypatch.setattr(lib_jira, "pulp_module", lambda: pulp)
    ok, detail = lib_jira.pulp_has_nvr("grafana13.1", "grafana13.1-13.1.1-1.src.rpm")
    assert ok
    assert "Pulp listing" in detail


def test_set_fixed_in_build_refuses_unpublished_nvr(monkeypatch) -> None:
    monkeypatch.setattr(lib_jira, "pulp_has_nvr", lambda pkg, nvr: (False, "missing"))
    try:
        helper.set_fixed_in_build("HUM-1", "pkg-1.0-1.src.rpm", "pkg")
    except RuntimeError as err:
        assert "Refusing" in str(err)
    else:
        raise AssertionError("expected RuntimeError")


def test_set_fixed_in_build_force_skips_pulp(monkeypatch) -> None:
    calls: list[tuple] = []

    def set_fib(base, token, ticket, build, *, basic_auth_user=None):
        calls.append((ticket, build, basic_auth_user))

    monkeypatch.setattr(lib_jira, "pulp_has_nvr", lambda pkg, nvr: (False, "missing"))
    monkeypatch.setattr(lib_jira, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        lib_jira,
        "jira_client_module",
        lambda: SimpleNamespace(set_fixed_in_build=set_fib),
    )
    detail = helper.set_fixed_in_build(
        "HUM-1", "pkg-1.0-1.src.rpm", "pkg", force=True
    )
    assert "forced" in detail
    assert calls[0][0] == "HUM-1"
    assert calls[0][1] == "pkg-1.0-1.src.rpm"


def test_apply_next_release_labels(monkeypatch) -> None:
    calls: list[str] = []

    def add_comment(*_a, **_k):
        calls.append("comment")
        return True

    def add_label(*a, **_k):
        calls.append(f"add:{a[3]}")

    def remove_label(*a, **_k):
        calls.append(f"remove:{a[3]}")

    monkeypatch.setattr(lib_jira, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        lib_jira,
        "jira_client_module",
        lambda: SimpleNamespace(
            jira_add_comment=add_comment,
            add_jira_label=add_label,
            remove_jira_label=remove_label,
        ),
    )
    helper.apply_next_release("HUM-9", "waiting on upstream\n")
    assert calls == [
        "comment",
        "add:cve-next-release",
        "remove:cve-needs-attention",
    ]


def test_close_not_a_bug(monkeypatch) -> None:
    calls: list[str] = []

    def add_comment(*_a: object, **_k: object) -> bool:
        calls.append("comment")
        return True

    def set_vex(*_a: object, **_k: object) -> bool:
        calls.append("vex")
        return True

    def resolve(*_a: object, **_k: object) -> bool:
        calls.append("resolve")
        return True

    monkeypatch.setattr(lib_jira, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        lib_jira,
        "jira_client_module",
        lambda: SimpleNamespace(
            jira_add_comment=add_comment,
            set_vex_justification=set_vex,
            jira_resolve_issue=resolve,
        ),
    )
    helper.close_not_a_bug("HUM-2", "not present\n", "Component not Present")
    assert calls == ["comment", "vex", "resolve"]


def test_close_not_a_bug_none_return_is_success(monkeypatch) -> None:
    monkeypatch.setattr(lib_jira, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        lib_jira,
        "jira_client_module",
        lambda: SimpleNamespace(
            jira_add_comment=lambda *_a, **_k: True,
            set_vex_justification=lambda *_a, **_k: None,
            jira_resolve_issue=lambda *_a, **_k: None,
        ),
    )
    helper.close_not_a_bug("HUM-3", "not present\n", "Component not Present")


def test_close_not_a_bug_false_return_raises(monkeypatch) -> None:
    monkeypatch.setattr(lib_jira, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        lib_jira,
        "jira_client_module",
        lambda: SimpleNamespace(
            jira_add_comment=lambda *_a, **_k: True,
            set_vex_justification=lambda *_a, **_k: False,
            jira_resolve_issue=lambda *_a, **_k: True,
        ),
    )
    try:
        helper.close_not_a_bug("HUM-4", "not present\n", "Component not Present")
    except RuntimeError as err:
        assert "VEX justification" in str(err)
    else:
        raise AssertionError("expected RuntimeError")


def test_parse_created_issue_key() -> None:
    assert (
        lib_jira.parse_created_issue_key(
            "https://redhat.atlassian.net/browse/HUM-6222\n"
        )
        == "HUM-6222"
    )


def test_gitlab_project_from_url() -> None:
    assert lib_vcs.gitlab_project_from_url(
        "git@gitlab.com:prarit/rpms.git"
    ) == "prarit/rpms"
    assert lib_vcs.gitlab_project_from_url(
        "https://gitlab.com/prarit/rpms.git"
    ) == "prarit/rpms"


def test_detect_fork_remote_skips_upstream(monkeypatch) -> None:
    output = "\n".join(
        [
            "origin\thttps://gitlab.com/redhat/hummingbird/rpms.git (fetch)",
            "origin\thttps://gitlab.com/redhat/hummingbird/rpms.git (push)",
            "prarit\thttps://gitlab.com/prarit/rpms.git (fetch)",
            "prarit\thttps://gitlab.com/prarit/rpms.git (push)",
        ]
    )
    monkeypatch.setattr(
        lib_vcs,
        "run_command",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=output, stderr=""),
    )
    name, project = lib_vcs.detect_fork_remote()
    assert name == "prarit"
    assert project == "prarit/rpms"


def test_lookaside_upload_commands() -> None:
    cmds = helper.lookaside_upload_commands(
        [Path("/var/tmp/foo.tar.gz")], "grafana13.1"
    )
    assert cmds[0] == "cp /var/tmp/foo.tar.gz /tmp/foo.tar.gz"
    assert cmds[1] == (
        "./ci/upload-to-lookaside-cache.sh -f /tmp/foo.tar.gz -p grafana13.1"
    )


def test_build_mr_description() -> None:
    text = helper.build_mr_description(
        "Bump foo for the CVE.",
        task="HUM-9",
        trackers=["HUM-1", "2"],
        cves=["CVE-2026-1"],
    )
    assert "Closes: HUM-9" in text
    assert "Ref: HUM-1, HUM-2" in text
    assert "CVE: CVE-2026-1" in text


def test_create_hum_task_links_and_starts(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_rhjira(args, **_k):
        calls.append(args)
        if args[0] == "create":
            return SimpleNamespace(
                returncode=0,
                stdout="https://redhat.atlassian.net/browse/HUM-7000\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lib_jira, "ensure_rhjira_available", lambda: None)
    monkeypatch.setattr(lib_jira, "run_rhjira", fake_rhjira)
    monkeypatch.setattr(
        lib_jira,
        "load_jira_auth",
        lambda: SimpleNamespace(basic_auth_user="dev@redhat.com"),
    )
    key = helper.create_hum_task("summary", blocks=["HUM-1"])
    assert key == "HUM-7000"
    assert calls[0][0] == "create"
    assert ["edit", "HUM-7000", "--noeditor", "--blocks", "HUM-1"] in calls
    assert ["edit", "HUM-7000", "--noeditor", "--status", "In Progress"] in calls


def test_strip_only_keyword() -> None:
    tickets, only = helper.strip_only_keyword(["HUM-1", "HUM-2", "only"])
    assert tickets == ["HUM-1", "HUM-2"]
    assert only is True
    tickets, only = helper.strip_only_keyword(["HUM-1"])
    assert tickets == ["HUM-1"]
    assert only is False


def test_recap_lines_asks_before_discovered() -> None:
    named = lib_models.TicketReport(
        "HUM-1", "CVE-2026-1 pkg: foo", "In Progress", "Bug", "me", "cve-needs-attention"
    )
    named.fixed_in_build = "foo-1-1.src.rpm"
    extra = lib_models.TicketReport(
        "HUM-2", "CVE-2026-1 pkg: bar", "New", "Bug", "bot", ""
    )
    extra.package_guess = "bar"
    gathered = lib_models.GatheredTickets(
        reports=[named, extra],
        user_provided=["HUM-1"],
        discovered=["HUM-2"],
    )
    text = "\n".join(helper.recap_lines(gathered))
    assert "HUM-1 [user_provided]" in text
    assert "HUM-2 [discovered]" in text
    assert "FIB is set but no task/MR is linked" in text
    assert "Related: HUM-2 (bar)" in text
    assert "Yes please" in text


def test_split_nvr_and_version_cmp() -> None:
    assert lib_spec.split_nvr("grafana13.1-13.1.1-0.5.src.rpm") == (
        "grafana13.1",
        "13.1.1",
        "0.5",
    )
    assert lib_spec.split_nvr("foo-1.2") == ("foo", "1.2", "")
    assert lib_spec.version_cmp("1.2.3", "1.2.4") == -1
    assert lib_spec.version_cmp("1.3", "1.2.9") == 1
    assert lib_spec.version_cmp("v1.0.0", "1.0.0") == 0
    assert lib_spec.version_cmp("not-a-version", "1.0") is None


def test_parse_affected_constraints_and_satisfies() -> None:
    constraints = lib_spec.parse_affected_constraints("< 1.2.3")
    assert constraints == [("<", "1.2.3")]
    assert lib_spec.version_satisfies("1.2.2", constraints) is True
    assert lib_spec.version_satisfies("1.2.3", constraints) is False
    span = lib_spec.parse_affected_constraints("1.0 through 1.2.2")
    assert lib_spec.version_satisfies("1.1.0", span) is True
    assert lib_spec.version_satisfies("1.3.0", span) is False
    both = lib_spec.parse_affected_constraints(">= 1.0, < 1.5")
    assert lib_spec.version_satisfies("1.4.9", both) is True
    assert lib_spec.version_satisfies("1.5.0", both) is False
    assert lib_spec.parse_affected_constraints("") == []
    assert lib_spec.version_satisfies("1.0", []) is None


def test_version_check_payload_hints(tmp_path: Path, monkeypatch) -> None:
    pkg = "foo"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{pkg}.spec").write_text(
        "Name: foo\nVersion: 1.2.2\nRelease: 3%{?dist}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    below = helper.version_check_payload(
        pkg, affected="< 1.2.3", fixed="1.2.3"
    )
    assert below["local_nvr"] == "foo-1.2.2-3"
    assert below["vs_fixed"] == "below"
    assert below["vs_affected"] == "in_range"
    assert below["hint"] == "possibly_below_fix"
    above = helper.version_check_payload(
        pkg, local_nvr="foo-1.2.3-1", fixed="1.2.3", affected="< 1.2.3"
    )
    assert above["vs_fixed"] == "at_or_above"
    assert above["vs_affected"] == "not_in_range"
    assert above["hint"] == "possibly_at_or_above_fix"
    conflicting = helper.version_check_payload(
        pkg, local_nvr="foo-1.5.0-1", fixed="1.5.0", affected=">= 1.0, <= 2.0"
    )
    assert conflicting["vs_fixed"] == "at_or_above"
    assert conflicting["vs_affected"] == "in_range"
    assert conflicting["hint"] == "conflicting_signals"
    assert "broad CVE range" in conflicting["note"]


def test_extract_analysis_nvr() -> None:
    block = "\n".join(
        [
            "ASSESSMENT: maybe",
            "  Hummingbird SRPM version: foo-1.2-3.src.rpm",
            "  Affected: < 1.3",
        ]
    )
    assert helper.extract_analysis_nvr(block) == "foo-1.2-3"


def test_compare_fix_age_and_parse_github_url() -> None:
    from datetime import datetime, timezone

    commit = datetime(2026, 1, 10, tzinfo=timezone.utc)
    tag = datetime(2026, 2, 1, tzinfo=timezone.utc)
    before = helper.compare_fix_age(commit, tag)
    assert before["verdict"] == "commit_at_or_before_tag"
    after = helper.compare_fix_age(tag, commit)
    assert after["verdict"] == "commit_after_tag"
    parsed = lib_vcs.parse_github_commit_ref(
        "https://github.com/foo/bar/commit/abc123def"
    )
    assert parsed == ("foo", "bar", "abc123def")
    dt = lib_vcs.parse_iso_datetime("2026-01-10T00:00:00Z")
    assert dt.tzinfo is not None


def test_resolve_fix_age_dates_from_flags() -> None:
    commit_dt, tag_dt, meta = helper.resolve_fix_age_dates(
        commit_date="2026-01-01T00:00:00Z",
        tag_date="2026-02-01T00:00:00Z",
    )
    assert commit_dt < tag_dt
    assert meta["commit_date_source"] == "flag"
    assert meta["tag_date_source"] == "flag"


def test_fetch_github_commit_date(monkeypatch) -> None:
    def fake_json(url, timeout=60.0, extra_headers=None):
        assert "commits/abc123" in url
        return {"commit": {"committer": {"date": "2026-03-01T12:00:00Z"}}}

    monkeypatch.setattr(lib_vcs, "http_get_json", fake_json)
    dt = lib_vcs.fetch_github_commit_date("foo", "bar", "abc123")
    assert dt.year == 2026
    assert dt.month == 3


def test_bump_release_matches_dist_git_rules() -> None:
    bump = lib_spec.bump_release
    assert bump("3") == "3.1"
    assert bump("3.1") == "3.2"
    assert bump("3.1", upstream_release="3.1") == "3.1.1"
    assert bump("3.1.1", upstream_release="3.1") == "3.1.2"
    assert bump("3.1", upstream_release="3") == "3.2"
    assert bump("0.1", upstream_release="0.1") == "0.1.1"
    assert bump("8.%{revision}") == "8.%{revision}.1"


def test_release_bump_payload(tmp_path: Path, monkeypatch) -> None:
    pkg = "mypkg"
    (tmp_path / "rpms" / pkg).mkdir(parents=True)
    (tmp_path / "metadata").mkdir()
    (tmp_path / "rpms" / pkg / f"{pkg}.spec").write_text(
        "Name: mypkg\nVersion: 1.0\nRelease: 3.1%{?dist}\n",
        encoding="utf-8",
    )
    (tmp_path / "metadata" / f"{pkg}.json").write_text(
        json.dumps({"modification_status": "modified", "release": "3.1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(lib_spec, "repo_root", lambda: tmp_path)
    payload = helper.release_bump_payload(pkg)
    assert payload["spec_release"] == "3.1"
    assert payload["metadata_release"] == "3.1"
    assert payload["proposed_spec_release"] == "3.1.1"
    assert payload["writes"] is False
    assert payload["modification_status"] == "modified"


def test_release_bump_autorelease(tmp_path: Path, monkeypatch) -> None:
    pkg = "auto"
    (tmp_path / "rpms" / pkg).mkdir(parents=True)
    (tmp_path / "metadata").mkdir()
    (tmp_path / "rpms" / pkg / f"{pkg}.spec").write_text(
        "Name: auto\nVersion: 1.0\nRelease: %autorelease\n",
        encoding="utf-8",
    )
    (tmp_path / "metadata" / f"{pkg}.json").write_text(
        json.dumps({"modification_status": "clean", "release": "5"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lib_utils, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(lib_spec, "repo_root", lambda: tmp_path)
    payload = helper.release_bump_payload(pkg)
    assert payload["uses_autorelease"] is True
    assert payload["proposed_spec_release"] == ""
    assert "%autorelease" in payload["hint"]
