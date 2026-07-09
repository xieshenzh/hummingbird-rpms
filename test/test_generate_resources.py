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

    (tmp_path / "ci" / "package-overrides.yaml").write_text("{}\n")

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


def _load_rpa_config(gen_module, index=0):
    """Load RPA config and global config from the config file."""
    config = gen_module.load_releng_config()
    return config["rpas"][index], config["global"]


class TestBuildRelengVariables:
    def test_basic_structure(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

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
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

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
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

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
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

        names = [c["component_name"] for c in variables["component_list"]]
        assert "alpha-main" in names
        assert "not-imported-main" not in names
        assert "also-missing-main" not in names

    def test_component_list_sorted(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

        names = [c["component_name"] for c in variables["component_list"]]
        assert names == sorted(names)

    def test_component_count_matches_rpms_dirs(self, gen_module, mock_repo):
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            rpa_config, global_config = _load_rpa_config(gen_module)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

        rpms_dirs = [d for d in (mock_repo / "rpms").iterdir() if d.is_dir()]
        assert len(variables["component_list"]) == len(rpms_dirs)


class TestGenerateReleng:
    @staticmethod
    def _get_single_rpa(gen_module, mock_repo):
        """Helper: generate and return the single public RPA doc."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            results = gen_module.generate_releng()
        assert len(results) == 1
        return yaml.safe_load(next(iter(results.values())))

    def test_produces_valid_yaml(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        assert doc is not None
        assert doc["kind"] == "ReleasePlanAdmission"

    def test_yaml_structure(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        assert doc["apiVersion"] == "appstudio.redhat.com/v1alpha1"
        assert doc["metadata"]["name"] == "hummingbird-rpms-tech-preview-staging"
        assert doc["metadata"]["namespace"] == "rhtap-releng-tenant"
        assert doc["spec"]["applications"] == ["rpms-main"]
        assert doc["spec"]["origin"] == "hummingbird-tenant"
        assert doc["spec"]["policy"] == "rpm-hummingbird-stage"

    def test_parameterized_pulp_values(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        pulp = doc["spec"]["data"]["pulp"]
        assert pulp["domain"] == "public-hummingbird-staging-unsigned"
        assert pulp["secretName"] == "hummingbird-pulp-credentials-staging-secret"

    def test_parameterized_repo_ids(self, gen_module, mock_repo):
        """Repository IDs use the parameterized pulp_signed_domain."""
        doc = self._get_single_rpa(gen_module, mock_repo)
        repos = doc["spec"]["data"]["mapping"]["rpm-repositories"]
        for repo in repos:
            assert repo["repository_id"].startswith("public-hummingbird-staging-")

    def test_parameterized_pipeline_revision(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        params = doc["spec"]["pipeline"]["pipelineRef"]["params"]
        revision_param = next(p for p in params if p["name"] == "revision")
        assert revision_param["value"] == "development"

    def test_parameterized_pipeline_url(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        params = doc["spec"]["pipeline"]["pipelineRef"]["params"]
        url_param = next(p for p in params if p["name"] == "url")
        assert url_param["value"] == "https://github.com/konflux-ci/release-service-catalog.git"

    def test_components_have_rpm_content_type(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        components = doc["spec"]["data"]["mapping"]["components"]
        for comp in components:
            assert comp["contentType"] == "rpm"

    def test_single_component_mode(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        assert doc["spec"]["data"]["singleComponentMode"] is True

    def test_data_sections_present(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        data = doc["spec"]["data"]
        assert data["intention"] in ("staging", "production")
        assert "sign" in data
        assert "pyxis" in data
        assert "atlas" in data
        assert "releaseNotes" in data
        assert "mapping" in data

    def test_pipeline_config(self, gen_module, mock_repo):
        doc = self._get_single_rpa(gen_module, mock_repo)
        pipeline = doc["spec"]["pipeline"]
        assert pipeline["serviceAccountName"] == "hummingbird-rpm-release-staging"
        params = {p["name"]: p["value"] for p in pipeline["pipelineRef"]["params"]}
        assert "push-rpms-to-pulp" in params["pathInRepo"]
        assert params["url"] == "https://github.com/konflux-ci/release-service-catalog.git"
        assert pipeline["pipelineRef"]["resolver"] == "git"


class TestGenerateRelengIntegration:
    """Integration tests using the real repo data."""

    def test_real_repo_component_count(self, gen_module):
        """All rpms/ directories produce components across all RPAs."""
        config = gen_module.load_releng_config()
        all_components = []
        for rpa_config in config["rpas"]:
            variables = gen_module.build_releng_variables(rpa_config, config["global"])
            all_components.extend(c["component_name"] for c in variables["component_list"])
        rpms_dirs = [
            d for d in (REPO_ROOT / "rpms").iterdir() if d.is_dir()
        ]
        assert len(all_components) == len(rpms_dirs)

    def test_real_repo_produces_valid_yaml(self, gen_module):
        results = gen_module.generate_releng()
        assert len(results) >= 1
        for name, output in results.items():
            doc = yaml.safe_load(output)
            assert doc["kind"] == "ReleasePlanAdmission"
        public_doc = yaml.safe_load(next(iter(results.values())))
        components = public_doc["spec"]["data"]["mapping"]["components"]
        assert len(components) > 300


class TestPrivateProductFiltering:
    """Tests for private_product-based component filtering."""

    @pytest.fixture
    def mock_repo_with_private(self, mock_repo):
        """Extend mock_repo with private_product overrides and multi-RPA config."""
        (mock_repo / "ci" / "package-overrides.yaml").write_text(
            textwrap.dedent("""\
            alpha:
              private_product: example
            """)
        )

        (mock_repo / "ci" / "konflux_rpa_config.yml").write_text(
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
              - name: hummingbird-rpms-private-example
                application_prefix: private-example-rpms
                release_org: registry.stage.redhat.io/hummingbird-tech-preview
                single_component_mode: true
                service_account_name: hummingbird-rpm-release-staging
                pulp_unsigned_domain: private-hummingbird-example-unsigned
                pulp_signed_domain: private-hummingbird-example
                pulp_secret_name: hummingbird-pulp-credentials-private-production-secret
                pipeline_revision: development
                pipeline_url: https://github.com/konflux-ci/release-service-catalog.git
                private_product: example
                component_filter:
                  path_prefix: rpms/
            """)
        )
        return mock_repo

    def test_public_rpa_excludes_private_packages(self, gen_module, mock_repo_with_private):
        """Public RPA automatically excludes packages with private_product."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo_with_private):
            rpa_config, global_config = _load_rpa_config(gen_module, index=0)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

        names = [c["component_name"] for c in variables["component_list"]]
        assert "alpha-main" not in names
        assert "beta-lib-main" in names
        assert "gamma-utils-main" in names

    def test_private_product_includes_only_matching(self, gen_module, mock_repo_with_private):
        """Private RPA with private_product: example includes only matching packages."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo_with_private):
            rpa_config, global_config = _load_rpa_config(gen_module, index=1)
            variables = gen_module.build_releng_variables(rpa_config, global_config)

        names = [c["component_name"] for c in variables["component_list"]]
        assert names == ["alpha-main"]

    def test_private_rpa_uses_private_pulp_domain(self, gen_module, mock_repo_with_private):
        """Private RPA renders with its own Pulp domain in repo IDs."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo_with_private):
            results = gen_module.generate_releng()

        assert "hummingbird-rpms-private-example" in results
        doc = yaml.safe_load(results["hummingbird-rpms-private-example"])
        repos = doc["spec"]["data"]["mapping"]["rpm-repositories"]
        for repo in repos:
            assert repo["repository_id"].startswith("private-hummingbird-example-")

    def test_multi_rpa_generates_all(self, gen_module, mock_repo_with_private):
        """generate_releng() produces one output per RPA entry."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo_with_private):
            results = gen_module.generate_releng()

        assert len(results) == 2
        assert "hummingbird-rpms-tech-preview-staging" in results
        assert "hummingbird-rpms-private-example" in results

        for name, output in results.items():
            doc = yaml.safe_load(output)
            assert doc["kind"] == "ReleasePlanAdmission"

    def test_total_components_across_rpas(self, gen_module, mock_repo_with_private):
        """All packages appear in exactly one RPA."""
        with patch.object(gen_module, "ROOT_DIR", mock_repo_with_private):
            config = gen_module.load_releng_config()
            all_components = []
            for rpa_config in config["rpas"]:
                variables = gen_module.build_releng_variables(rpa_config, config["global"])
                all_components.extend(c["component_name"] for c in variables["component_list"])

        rpms_dirs = [d for d in (mock_repo_with_private / "rpms").iterdir() if d.is_dir()]
        assert len(all_components) == len(rpms_dirs)
        assert len(set(all_components)) == len(all_components)

class TestPrivateProductKonfluxVariables:
    """Tests for per-component application names in Konflux variables."""

    def test_private_product_sets_application_name(self, gen_module, tmp_path):
        """Packages with private_product get a per-component application_name."""
        (tmp_path / "rpms" / "mypkg").mkdir(parents=True)
        (tmp_path / "rpms" / "normalpkg").mkdir(parents=True)
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "package-overrides.yaml").write_text(
            textwrap.dedent("""\
            mypkg:
              private_product: example
            """)
        )

        with patch.object(gen_module, "ROOT_DIR", tmp_path):
            variables = gen_module.build_konflux_variables("main", "hummingbird-tenant", "https://example.com/rpms.git")

        rpms_by_name = {r["name"]: r for r in variables["rpms"]}
        assert rpms_by_name["mypkg"]["application_name"] == "private-example-rpms-main"
        assert "application_name" not in rpms_by_name["normalpkg"]

    def test_default_application_name_unchanged(self, gen_module, tmp_path):
        """Global application_name is still rpms-{branch}."""
        (tmp_path / "rpms" / "pkg").mkdir(parents=True)
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "package-overrides.yaml").write_text("{}\n")

        with patch.object(gen_module, "ROOT_DIR", tmp_path):
            variables = gen_module.build_konflux_variables("main", "hummingbird-tenant", "https://example.com/rpms.git")

        assert variables["application_name"] == "rpms-main"
        assert "application_name" not in variables["rpms"][0]


class TestValidation:
    """Tests for configuration validation."""

    def test_unknown_private_product_raises(self, gen_module, mock_repo):
        """private_product value with no matching RPA raises ValueError."""
        (mock_repo / "ci" / "package-overrides.yaml").write_text(
            textwrap.dedent("""\
            alpha:
              private_product: nonexistent
            """)
        )
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            with pytest.raises(ValueError, match="nonexistent"):
                gen_module.generate_releng()

    def test_missing_rpa_field_raises(self, gen_module, mock_repo):
        """Missing required field in RPA config raises ValueError."""
        (mock_repo / "ci" / "konflux_rpa_config.yml").write_text(
            textwrap.dedent("""\
            global:
              branch: main
              tenant: hummingbird-tenant
              release_tenant: rhtap-releng-tenant
              git_repo: https://gitlab.com/redhat/hummingbird/rpms.git
            rpas:
              - name: incomplete-rpa
                application_prefix: rpms
            """)
        )
        with patch.object(gen_module, "ROOT_DIR", mock_repo):
            with pytest.raises(ValueError, match="incomplete-rpa.*release_org"):
                gen_module.generate_releng()
