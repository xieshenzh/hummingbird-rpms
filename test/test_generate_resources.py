"""Tests for generate_resources.py releng generation."""

import shutil
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def gen_module():
    """Import generate_resources as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_resources",
        REPO_ROOT / "ci" / "generate_resources.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mock_repo(tmp_path):
    """Create a minimal repo tree for releng generation."""
    (tmp_path / "rpms" / "alpha").mkdir(parents=True)
    (tmp_path / "rpms" / "beta-lib").mkdir(parents=True)
    (tmp_path / "rpms" / "gamma_utils").mkdir(parents=True)

    (tmp_path / "ci").mkdir()
    (tmp_path / "ci" / "konflux_rpa_config.yml").write_text(
        textwrap.dedent("""\
        global:
          branch: main
          tenant: hummingbird-tenant
          release_tenant: rhtap-releng-tenant
          git_repo: https://gitlab.com/redhat/hummingbird/rpms.git
        rpas:
          - name: hummingbird-rpms-tech-preview-staging
            application_prefix: rpms
            release_org: registry.stage.redhat.io/hummingbird-tech-preview
            single_component_mode: true
            service_account_name: hummingbird-rpm-release-staging
            pulp_unsigned_domain: public-hummingbird-staging-unsigned
            pulp_signed_domain: public-hummingbird-staging
            pulp_secret_name: hummingbird-pulp-credentials-staging-secret
            pipeline_revision: development
            pipeline_url: https://github.com/konflux-ci/release-service-catalog.git
            component_filter:
              path_prefix: rpms/
              exclude_patterns: []
        """)
    )

    tmpl_dir = tmp_path / "konflux-templates"
    tmpl_dir.mkdir()
    macros_dir = tmpl_dir / "macros" / "releng"
    macros_dir.mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "konflux-templates" / "macros" / "releng" / "release-plan-admission.yml.j2",
        macros_dir / "release-plan-admission.yml.j2",
    )
    shutil.copy(
        REPO_ROOT / "konflux-templates" / "releng-staging.yml.j2",
        tmpl_dir / "releng-staging.yml.j2",
    )

    return tmp_path


class TestExpandTaskRunSpecs:
    """Tests for expand_task_run_specs (rpmbuild shorthand for both arches)."""

    def test_rpmbuild_expands_to_both_arches(self, gen_module):
        specs = [
            {
                "pipelineTaskName": "rpmbuild",
                "stepSpecs": [
                    {
                        "name": "run-syft",
                        "computeResources": {
                            "requests": {"memory": "10Gi"},
                            "limits": {"memory": "10Gi"},
                        },
                    }
                ],
            }
        ]
        out = gen_module.expand_task_run_specs(specs)
        assert len(out) == 2
        assert out[0]["pipelineTaskName"] == "rpmbuild-x86-64"
        assert out[1]["pipelineTaskName"] == "rpmbuild-aarch64"
        assert out[0]["stepSpecs"] == out[1]["stepSpecs"] == specs[0]["stepSpecs"]

    def test_other_task_names_unchanged(self, gen_module):
        specs = [{"pipelineTaskName": "upload-to-quay", "stepSpecs": [{"name": "foo"}]}]
        assert gen_module.expand_task_run_specs(specs) == specs

    def test_mixed_list(self, gen_module):
        specs = [
            {"pipelineTaskName": "rpmbuild", "stepSpecs": [{"name": "run-syft"}]},
            {"pipelineTaskName": "custom-task", "computeResources": {"limits": {"memory": "2Gi"}}},
        ]
        out = gen_module.expand_task_run_specs(specs)
        assert len(out) == 3
        assert [s["pipelineTaskName"] for s in out] == [
            "rpmbuild-x86-64",
            "rpmbuild-aarch64",
            "custom-task",
        ]


class TestBuildRelengVariables:
    def test_basic_structure(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        assert variables["name"] == "hummingbird-rpms-tech-preview-staging"
        assert variables["application_prefix"] == "rpms"
        assert variables["release_org"] == "registry.stage.redhat.io/hummingbird-tech-preview"
        assert variables["single_component_mode"] is True
        assert variables["service_account_name"] == "hummingbird-rpm-release-staging"
        assert variables["release_tenant"] == "rhtap-releng-tenant"
        assert variables["branch"] == "main"
        assert variables["tenant"] == "hummingbird-tenant"
        assert variables["pulp_unsigned_domain"] == "public-hummingbird-staging-unsigned"
        assert variables["pulp_signed_domain"] == "public-hummingbird-staging"
        assert variables["pulp_secret_name"] == "hummingbird-pulp-credentials-staging-secret"
        assert variables["pipeline_revision"] == "development"
        assert variables["pipeline_url"] == "https://github.com/konflux-ci/release-service-catalog.git"

    def test_component_list_from_packages(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        names = [c["component_name"] for c in variables["component_list"]]
        assert "alpha-main" in names
        assert "beta-lib-main" in names
        assert "gamma-utils-main" in names

    def test_component_name_sanitization(self, gen_module, mock_repo):
        """Underscores, dots, and plus signs are replaced with hyphens."""
        (mock_repo / "rpms" / "foo_bar").mkdir()
        (mock_repo / "rpms" / "baz+qux").mkdir()
        (mock_repo / "rpms" / "lib.name").mkdir()

        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        names = [c["component_name"] for c in variables["component_list"]]
        assert "foo-bar-main" in names
        assert "baz-qux-main" in names
        assert "lib-name-main" in names
        assert not any("_" in n or "+" in n or n.count("..") for n in names)

    def test_component_filter_excludes_unimported(self, gen_module, mock_repo):
        """Packages in target-packages.yml without rpms/ dirs are excluded."""
        (mock_repo / "target-packages.yml").write_text(
            yaml.dump({"packages": ["alpha", "not-imported", "also-missing"]})
        )

        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        names = [c["component_name"] for c in variables["component_list"]]
        assert "alpha-main" in names
        assert "not-imported-main" not in names
        assert "also-missing-main" not in names

    def test_component_list_sorted(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        names = [c["component_name"] for c in variables["component_list"]]
        assert names == sorted(names)

    def test_component_count_matches_rpms_dirs(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            variables = gen_module.build_releng_variables()

        rpms_dirs = [d for d in (mock_repo / "rpms").iterdir() if d.is_dir()]
        assert len(variables["component_list"]) == len(rpms_dirs)


class TestGenerateReleng:
    def test_produces_valid_yaml(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        assert doc is not None
        assert doc["kind"] == "ReleasePlanAdmission"

    def test_yaml_structure(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        assert doc["apiVersion"] == "appstudio.redhat.com/v1alpha1"
        assert doc["metadata"]["name"] == "hummingbird-rpms-tech-preview-staging"
        assert doc["metadata"]["namespace"] == "rhtap-releng-tenant"
        assert doc["spec"]["applications"] == ["rpms-main"]
        assert doc["spec"]["origin"] == "hummingbird-tenant"
        assert doc["spec"]["policy"] == "rpm-hummingbird-stage"

    def test_parameterized_pulp_values(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        pulp = doc["spec"]["data"]["pulp"]
        assert pulp["domain"] == "public-hummingbird-staging-unsigned"
        assert pulp["secretName"] == "hummingbird-pulp-credentials-staging-secret"

    def test_parameterized_pipeline_revision(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        params = doc["spec"]["pipeline"]["pipelineRef"]["params"]
        revision_param = next(p for p in params if p["name"] == "revision")
        assert revision_param["value"] == "development"

    def test_parameterized_pipeline_url(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        params = doc["spec"]["pipeline"]["pipelineRef"]["params"]
        url_param = next(p for p in params if p["name"] == "url")
        assert url_param["value"] == "https://github.com/konflux-ci/release-service-catalog.git"

    def test_components_have_rpm_content_type(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        components = doc["spec"]["data"]["mapping"]["components"]
        for comp in components:
            assert comp["contentType"] == "rpm"

    def test_single_component_mode(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        assert doc["spec"]["data"]["singleComponentMode"] is True

    def test_data_sections_present(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        data = doc["spec"]["data"]
        assert data["intention"] in ("staging", "production")
        assert "sign" in data
        assert "pyxis" in data
        assert "atlas" in data
        assert "releaseNotes" in data
        assert "mapping" in data

    def test_pipeline_config(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            output = gen_module.generate_releng()

        doc = yaml.safe_load(output)
        pipeline = doc["spec"]["pipeline"]
        assert pipeline["serviceAccountName"] == "hummingbird-rpm-release-staging"
        params = {p["name"]: p["value"] for p in pipeline["pipelineRef"]["params"]}
        assert "push-rpms-to-pulp" in params["pathInRepo"]
        assert params["url"] == "https://github.com/konflux-ci/release-service-catalog.git"
        assert pipeline["pipelineRef"]["resolver"] == "git"


class TestGenerateRelengIntegration:
    """Integration tests using the real repo data."""

    def test_real_repo_component_count(self, gen_module):
        """All rpms/ directories produce components."""
        variables = gen_module.build_releng_variables()
        rpms_dirs = [
            d for d in (REPO_ROOT / "rpms").iterdir() if d.is_dir()
        ]
        assert len(variables["component_list"]) == len(rpms_dirs)

    def test_real_repo_produces_valid_yaml(self, gen_module):
        output = gen_module.generate_releng()
        doc = yaml.safe_load(output)
        assert doc["kind"] == "ReleasePlanAdmission"
        components = doc["spec"]["data"]["mapping"]["components"]
        assert len(components) > 300
