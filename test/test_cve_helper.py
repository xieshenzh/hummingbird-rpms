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


def test_normalize_argv_inserts_show_for_bare_tickets() -> None:
    assert helper.normalize_argv(["HUM-1234", "--title-only"]) == [
        "show",
        "HUM-1234",
        "--title-only",
    ]
    assert helper.normalize_argv(["bot-mrs", "boost"]) == ["bot-mrs", "boost"]


def test_pulp_sbom_package_dir_replaces_dots() -> None:
    assert helper.pulp_sbom_package_dir("grafana13.1") == "grafana13-1-main"
    assert helper.pulp_sbom_package_dir("boost") == "boost-main"


def test_parse_glab_mr_list() -> None:
    output = "\n".join(
        [
            "!3905  grafana13.1: Update axios (renovate) (renovate/axios)",
            "No open merge requests match your search",
            "!12  plain title",
        ]
    )
    entries = helper._parse_glab_mr_list(output, "open")
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
    monkeypatch.setattr(helper, "repo_root", lambda: tmp_path)
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

    monkeypatch.setattr(helper, "run_command", fake_run)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    result = helper.run_rhjira(["show", "HUM-1"])
    assert result.returncode == 0
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_run_rhjira_does_not_retry_non_transient(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, *, cwd=None):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="issue not found")

    monkeypatch.setattr(helper, "run_command", fake_run)
    result = helper.run_rhjira(["show", "HUM-1"])
    assert result.returncode == 1
    assert len(calls) == 1


def test_read_local_spec_nvr(tmp_path: Path, monkeypatch) -> None:
    pkg = "mypkg"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    # Spec with a %global and an external-override conditional pattern
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
    monkeypatch.setattr(helper, "repo_root", lambda: tmp_path)
    assert helper.read_local_spec_nvr(pkg) == "mypkg-2.5.1-3"
    # Missing package returns empty string
    assert helper.read_local_spec_nvr("nonexistent") == ""


def test_search_cve_in_spec_and_patches(tmp_path: Path, monkeypatch) -> None:
    pkg = "testpkg"
    pkg_dir = tmp_path / "rpms" / pkg
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{pkg}.spec").write_text("Name: testpkg\nVersion: 1.0\n", encoding="utf-8")
    (pkg_dir / "fix-cve.patch").write_text(
        "# Backport for CVE-2024-12345\n--- a/foo.c\n+++ b/foo.c\n", encoding="utf-8"
    )
    monkeypatch.setattr(helper, "repo_root", lambda: tmp_path)
    # Found in patch
    assert helper.search_cve_in_spec_and_patches(pkg, ["CVE-2024-12345"]) is True
    # Not found
    assert helper.search_cve_in_spec_and_patches(pkg, ["CVE-2099-99999"]) is False
    # Missing package dir
    assert helper.search_cve_in_spec_and_patches("ghost", ["CVE-2024-12345"]) is False


def test_build_suggested_chat_title() -> None:
    reports = [
        helper.TicketReport(
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
    monkeypatch.setattr(helper, "pulp_module", lambda: pulp)
    ok, detail = helper.pulp_has_nvr("grafana13.1", "grafana13.1-13.1.1-1.src.rpm")
    assert ok
    assert "Pulp listing" in detail


def test_set_fixed_in_build_refuses_unpublished_nvr(monkeypatch) -> None:
    monkeypatch.setattr(helper, "pulp_has_nvr", lambda pkg, nvr: (False, "missing"))
    monkeypatch.setattr(helper, "load_jira_auth", _fake_auth)
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

    monkeypatch.setattr(helper, "pulp_has_nvr", lambda pkg, nvr: (False, "missing"))
    monkeypatch.setattr(helper, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        helper,
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

    monkeypatch.setattr(helper, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        helper,
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

    monkeypatch.setattr(helper, "load_jira_auth", _fake_auth)
    monkeypatch.setattr(
        helper,
        "jira_client_module",
        lambda: SimpleNamespace(
            jira_add_comment=lambda *_a, **_k: calls.append("comment") or True,
            set_vex_justification=lambda *_a, **_k: calls.append("vex") or True,
            jira_resolve_issue=lambda *_a, **_k: calls.append("resolve") or True,
        ),
    )
    helper.close_not_a_bug("HUM-2", "not present\n", "Component not Present")
    assert calls == ["comment", "vex", "resolve"]


