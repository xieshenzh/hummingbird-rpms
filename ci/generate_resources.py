#!/usr/bin/env python3
"""
Fast resource generator for Tekton/Konflux templates.

This replaces the shell scripts generate_pac_resources.sh and
generate_konflux_resources.sh with a Python implementation that
avoids spawning thousands of yq subprocesses.
"""

import argparse
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT_DIR = Path(__file__).resolve().parent.parent

# Default configuration
DEFAULT_TIMEOUT_HOURS = 4
DEFAULT_BUILD_PLATFORMS = ["linux/x86_64", "linux/arm64"]
BUILD_TRIGGER_RPM_NAME = "setup"

# Konflux rpmbuild-pipeline uses one pipeline task per arch (not a single "rpmbuild" task).
# package-overrides may use pipelineTaskName: rpmbuild as shorthand for both.
RPMBUILD_PIPELINE_TASK_NAMES = ("rpmbuild-x86-64", "rpmbuild-aarch64")


def expand_task_run_specs(specs: list) -> list:
    """Expand shorthand task names in task_run_specs for generated PipelineRuns."""
    expanded: list = []
    for spec in specs:
        name = spec.get("pipelineTaskName")
        if name == "rpmbuild":
            for task_name in RPMBUILD_PIPELINE_TASK_NAMES:
                dup = dict(spec)
                dup["pipelineTaskName"] = task_name
                expanded.append(dup)
        else:
            expanded.append(spec)
    return expanded

# renovate: datasource=docker depName=quay.io/hummingbird-ci/rpmbuild-pipeline
PIPELINE_BUNDLE = "quay.io/hummingbird-ci/rpmbuild-pipeline:latest@sha256:bf43eccaf6669073b4ef441555c004a7ae263a1ec027530decdba2f3714f23ae"

def load_yaml_file(path: Path) -> dict | None:
    """Load a YAML file, returning None if it doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)  # type: ignore[return-value]


def get_all_packages() -> list[str]:
    """Get sorted list of all package names from manifest and rpms/ directory."""
    packages = set()

    # From target-packages.yml
    manifest = load_yaml_file(ROOT_DIR / "target-packages.yml")
    if manifest and "packages" in manifest:
        packages.update(manifest["packages"])

    # From rpms/ directories
    rpms_dir = ROOT_DIR / "rpms"
    if rpms_dir.exists():
        for entry in rpms_dir.iterdir():
            if entry.is_dir():
                packages.add(entry.name)

    return sorted(packages)


def sanitize_component_name(name: str) -> str:
    """Sanitize package name for use as Kubernetes resource name."""
    # resource names must not contain underscores, uppercase, +, or .
    result = name.replace("_", "-").replace("+", "-").replace(".", "-")
    return result.lower()


def build_pac_variables(branch: str, tenant: str, resource_type: str) -> dict:
    """Build variables for PAC (Pipeline as Code) templates."""
    packages = get_all_packages()
    package_overrides = load_yaml_file(ROOT_DIR / "ci" / "package-overrides.yaml") or {}

    application_name = f"rpms-{branch}"

    rpms = []
    for dname in packages:
        imported = (ROOT_DIR / "rpms" / dname).is_dir()
        component_name = sanitize_component_name(dname)
        name = f"{component_name}-{branch}"

        rpm_data = {
            "name": name,
            "dname": dname,
            "imported": imported,
            "timeout_hours": DEFAULT_TIMEOUT_HOURS,
        }

        if imported:
            # Auto-detect spec file in package directory
            pkg_dir = ROOT_DIR / "rpms" / dname
            spec_files = list(pkg_dir.glob("*.spec"))
            if spec_files:
                rpm_data["specfile"] = spec_files[0].name
            else:
                # Fallback to default naming if no spec file found
                rpm_data["specfile"] = f"{dname}.spec"

            # Check for overrides
            pkg_config = package_overrides.get(dname, {})

            # Determine the upstream package name for Tekton pipeline
            #
            # For most packages, the directory name (dname) matches the package name.
            # However, for renamed packages (e.g., golang1.25, ruby3.3, tomcat10),
            # we need the upstream package name for lookaside cache and SRPM naming.
            #
            # When forked_from points to hummingbird, we use the directory name
            # since the hummingbird lookaside cache uses hummingbird package names.
            #
            # Native packages (Hummingbird-specific) have no source URL in metadata,
            # so we use the directory name directly.
            metadata_file = ROOT_DIR / "metadata" / f"{dname}.json"
            metadata = load_yaml_file(metadata_file)
            upstream_name = dname  # Default to directory name

            forked_from = pkg_config.get("forked_from", "")
            is_hummingbird = "hummingbird" in forked_from

            if metadata and metadata.get("modification_status") == "native":
                # Native packages: Use directory name (no upstream source)
                # Examples: chunkah, hummingbird-release, openssl-fips-provider
                upstream_name = dname
            elif is_hummingbird:
                # Hummingbird lookaside cache: Use directory name
                # Examples: nss-fips
                upstream_name = dname
            elif metadata and "source" in metadata:
                # Fedora/CentOS packages: Extract from source URL to handle renamed packages
                # Examples:
                #   - rpms/golang1.25 -> source: .../golang.git -> upstream_name: golang
                #   - rpms/ruby3.3 -> source: .../ruby.git -> upstream_name: ruby
                #   - rpms/tomcat10 -> source: .../tomcat.git -> upstream_name: tomcat
                upstream_name = Path(metadata["source"]).stem

            # Store upstream package name for use in pipeline
            rpm_data["package_name"] = upstream_name

            if "timeout_hours" in pkg_config:
                rpm_data["timeout_hours"] = pkg_config["timeout_hours"]

            # Always include build_platforms, using override or defaults
            rpm_data["build_platforms"] = pkg_config.get(
                "build_platforms", DEFAULT_BUILD_PLATFORMS
            )

            if "task_run_specs" in pkg_config:
                rpm_data["task_run_specs"] = expand_task_run_specs(
                    pkg_config["task_run_specs"]
                )

            if "forked_from" in pkg_config:
                rpm_data["forked_from"] = pkg_config["forked_from"]

            private_product = pkg_config.get("private_product")
            if private_product:
                rpm_data["application_name"] = f"private-{private_product}-rpms-{branch}"

            extra_params = {"purl-rpm-namespace": "redhat"}
            if "extra_params" in pkg_config:
                extra_params.update(pkg_config["extra_params"])
            if resource_type == "pull-request":
                extra_params["image-expires-after"] = "5d"
            rpm_data["extra_params"] = extra_params

            if "lookaside_cache_url" in pkg_config:
                rpm_data["lookaside_cache_url"] = pkg_config["lookaside_cache_url"]

            # Extra path changes
            extra_paths = []

            # Add package-specific test file if it exists
            test_file = ROOT_DIR / "test" / "rpms" / f"{dname}.yml"
            if test_file.exists():
                extra_paths.append(f"test/rpms/{dname}.yml")

            # Add ci/ and mock/ path changes for canary rpm
            if dname == BUILD_TRIGGER_RPM_NAME and resource_type == "pull-request":
                extra_paths.extend(["ci/***", "mock/***"])

            if extra_paths:
                rpm_data["extra_path_changes"] = extra_paths

        rpms.append(rpm_data)

    return {
        "branch": branch,
        "application_name": application_name,
        "pipeline_bundle": PIPELINE_BUNDLE,
        "tenant": tenant,
        "rpms": rpms,
    }


def build_konflux_variables(branch: str, tenant: str, git_repo: str) -> dict:
    """Build variables for Konflux templates."""
    packages = get_all_packages()
    package_overrides = load_yaml_file(ROOT_DIR / "ci" / "package-overrides.yaml") or {}

    application_name = f"rpms-{branch}"

    rpms = []
    for name in packages:
        imported = (ROOT_DIR / "rpms" / name).is_dir()
        component_name = sanitize_component_name(name)

        rpm_data = {
            "name": name,
            "imported": imported,
            "component_name": f"{component_name}-{branch}",
            "repository": name,
            "tags": ["latest"],
        }

        if imported:
            pkg_config = package_overrides.get(name, {})
            private_product = pkg_config.get("private_product")
            if private_product:
                rpm_data["application_name"] = f"private-{private_product}-rpms-{branch}"

            # Load properties.yml if it exists (only for imported packages)
            properties_file = ROOT_DIR / "rpms" / name / "properties.yml"
            if properties_file.exists():
                properties = load_yaml_file(properties_file)
                if properties:
                    rpm_data.update(properties)

        rpms.append(rpm_data)

    return {
        "application_name": application_name,
        "branch": branch,
        "tenant": tenant,
        "git_repo": git_repo,
        "rpms": rpms,
    }


_REQUIRED_RPA_FIELDS = [
    "name", "application_prefix", "release_org", "single_component_mode",
    "service_account_name", "pulp_unsigned_domain", "pulp_signed_domain",
    "pulp_secret_name", "pipeline_revision", "pipeline_url",
]


def build_releng_variables(rpa_config: dict, global_config: dict) -> dict:
    """Build variables for a single ReleasePlanAdmission."""
    rpa_name = rpa_config.get("name", "<unnamed>")
    for field in _REQUIRED_RPA_FIELDS:
        if field not in rpa_config:
            raise ValueError(
                f"RPA '{rpa_name}' is missing required field '{field}' "
                f"in ci/konflux_rpa_config.yml"
            )

    package_overrides = load_yaml_file(ROOT_DIR / "ci" / "package-overrides.yaml") or {}

    packages = get_all_packages()

    component_filter = rpa_config.get("component_filter", {})
    path_prefix = component_filter.get("path_prefix", "")
    if path_prefix:
        filter_dir = ROOT_DIR / path_prefix.rstrip("/")
        packages = [pkg for pkg in packages if (filter_dir / pkg).is_dir()]

    # Filter by private_product assignment
    # - Private RPAs (with private_product set): include only matching packages
    # - Non-private RPAs: automatically exclude packages that have a private_product
    private_product = rpa_config.get("private_product")

    if private_product:
        packages = [
            pkg for pkg in packages
            if package_overrides.get(pkg, {}).get("private_product") == private_product
        ]
    else:
        packages = [
            pkg for pkg in packages
            if "private_product" not in package_overrides.get(pkg, {})
        ]

    component_list = [
        {"component_name": f"{sanitize_component_name(pkg)}-{global_config['branch']}"}
        for pkg in packages
    ]

    return {
        "name": rpa_config["name"],
        "application_prefix": rpa_config["application_prefix"],
        "release_org": rpa_config["release_org"],
        "single_component_mode": rpa_config["single_component_mode"],
        "service_account_name": rpa_config["service_account_name"],
        "component_list": component_list,
        "release_tenant": global_config["release_tenant"],
        "branch": global_config["branch"],
        "tenant": global_config["tenant"],
        "pulp_unsigned_domain": rpa_config["pulp_unsigned_domain"],
        "pulp_signed_domain": rpa_config["pulp_signed_domain"],
        "pulp_secret_name": rpa_config["pulp_secret_name"],
        "pipeline_revision": rpa_config["pipeline_revision"],
        "pipeline_url": rpa_config["pipeline_url"],
    }


def load_releng_config() -> dict:
    """Load the releng RPA configuration file."""
    config_path = ROOT_DIR / "ci" / "konflux_rpa_config.yml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def generate_releng() -> dict[str, str]:
    """Generate releng (ReleasePlanAdmission) resources for all configured RPAs."""
    config = load_releng_config()
    global_config = config["global"]

    package_overrides = load_yaml_file(ROOT_DIR / "ci" / "package-overrides.yaml") or {}
    known_products = {
        rpa["private_product"]
        for rpa in config["rpas"]
        if "private_product" in rpa
    }
    for pkg, cfg in package_overrides.items():
        product = cfg.get("private_product") if isinstance(cfg, dict) else None
        if product and product not in known_products:
            raise ValueError(
                f"Package '{pkg}' has private_product: '{product}' but no RPA "
                f"entry has private_product: '{product}'. "
                f"Known products: {sorted(known_products)}"
            )

    template_path = ROOT_DIR / "konflux-templates" / "releng-staging.yml.j2"
    macros_dir = ROOT_DIR / "konflux-templates" / "macros" / "releng"

    results = {}
    for rpa_config in config["rpas"]:
        print(f"Building releng variables for {rpa_config['name']}...", file=sys.stderr)
        variables = build_releng_variables(rpa_config, global_config)

        print(f"Rendering {rpa_config['name']}...", file=sys.stderr)
        result = render_template(template_path, macros_dir, variables)
        if not result.endswith("\n"):
            result += "\n"
        results[rpa_config["name"]] = result

    return results


def render_template(template_path: Path, macros_dir: Path, variables: dict) -> str:
    """Render a Jinja2 template with the given variables."""
    # Load macros and template
    macros_content = []
    if macros_dir.exists():
        for macro_file in (
            sorted(macros_dir.glob("*.j2"))
            if macros_dir.name == "macros"
            else sorted(macros_dir.glob("*.yml.j2"))
        ):
            macros_content.append(macro_file.read_text())

    template_content = template_path.read_text()
    full_template = "\n".join(macros_content) + "\n" + template_content

    # Create Jinja2 environment
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
    )

    template = env.from_string(full_template)
    return template.render(**variables)


def generate_pac(resource_type: str, branch: str, tenant: str) -> str:
    """Generate PAC resources."""
    if resource_type not in ("push", "pull-request"):
        sys.exit(f"ERROR: Invalid resource type: {resource_type}")

    print("Building template variables...", file=sys.stderr)
    variables = build_pac_variables(branch, tenant, resource_type)

    print("Rendering Pipeline as Code resources...", file=sys.stderr)
    template_path = ROOT_DIR / ".tekton" / f"rpms-on-{resource_type}.yaml.j2"
    macros_dir = ROOT_DIR / ".tekton" / "macros"

    return render_template(template_path, macros_dir, variables)


def generate_konflux(branch: str, tenant: str, git_repo: str) -> str:
    """Generate Konflux resources."""
    print("Building template variables...", file=sys.stderr)
    variables = build_konflux_variables(branch, tenant, git_repo)

    print("Rendering Konflux resources...", file=sys.stderr)
    template_path = ROOT_DIR / "konflux-templates" / "konflux-resources.yml.j2"
    macros_dir = ROOT_DIR / "konflux-templates" / "macros"

    return render_template(template_path, macros_dir, variables)


def main():
    parser = argparse.ArgumentParser(description="Generate Tekton/Konflux resources")
    parser.add_argument("--branch", default="main", help="Git branch (default: main)")
    parser.add_argument("--tenant", default="hummingbird-tenant", help="Konflux tenant")
    parser.add_argument(
        "--git-repo",
        default="https://gitlab.com/redhat/hummingbird/rpms.git",
        help="Git repository URL",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # PAC subcommand
    pac_parser = subparsers.add_parser(
        "pac", help="Generate Pipeline as Code resources"
    )
    pac_parser.add_argument(
        "resource_type",
        choices=["push", "pull-request"],
        help="Resource type to generate",
    )

    # Konflux subcommand
    subparsers.add_parser("konflux", help="Generate Konflux resources")

    # Releng subcommand
    subparsers.add_parser("releng", help="Generate releng resources")

    # All subcommand (replaces generate.sh)
    subparsers.add_parser("all", help="Generate all resources (replaces generate.sh)")

    args = parser.parse_args()

    if args.command == "pac":
        print(generate_pac(args.resource_type, args.branch, args.tenant))
    elif args.command == "konflux":
        print(generate_konflux(args.branch, args.tenant, args.git_repo))
    elif args.command == "releng":
        releng_results = generate_releng()
        for name, content in releng_results.items():
            print(f"--- {name} ---", file=sys.stderr)
            print(content)
    elif args.command == "all":
        # Generate all resources (equivalent to generate.sh)
        pac_push = generate_pac("push", args.branch, args.tenant)
        (ROOT_DIR / ".tekton" / "rpms-on-push.yaml").write_text(pac_push)

        pac_pr = generate_pac("pull-request", args.branch, args.tenant)
        (ROOT_DIR / ".tekton" / "rpms-on-pull-request.yaml").write_text(pac_pr)

        konflux = generate_konflux(args.branch, args.tenant, args.git_repo)
        (ROOT_DIR / "konflux-templates" / "rendered.yml").write_text(konflux)

        releng_results = generate_releng()
        releng_dir = ROOT_DIR / "releng"
        releng_dir.mkdir(exist_ok=True)
        for name, content in releng_results.items():
            (releng_dir / f"{name}.yaml").write_text(content)

        print("Generated all resources.", file=sys.stderr)


if __name__ == "__main__":
    main()
