#!/usr/bin/python3
"""Refresh bundled npm Provides from Grafana's Yarn lockfile."""

import json
import re
import sys
import tarfile
from pathlib import Path

import yaml

BEGIN = "# BEGIN generated bundled npm Provides"
END = "# END generated bundled npm Provides"


def rpm_version(version):
    """Translate npm prerelease syntax to an RPM-compatible EVR."""
    base, separator, prerelease = version.partition("-")
    if separator:
        prerelease = prerelease.lstrip("-._+~").replace("-", ".")
        version = base + "~" + prerelease
    return re.sub(r"([._+~])\1+", r"\1", version)


def dependencies(package):
    for section in ("dependencies", "optionalDependencies"):
        yield from package.get(section, {}).items()


def descriptor_name(descriptor):
    for protocol in ("@npm:", "@patch:", "@portal:", "@file:"):
        if protocol in descriptor:
            return descriptor.partition(protocol)[0]
    return ""


def yarn_packages(lock, root_package, workspaces):
    data = yaml.safe_load(lock)
    descriptors = {}
    packages_by_name = {}
    for key, package in data.items():
        if key == "__metadata":
            continue
        for descriptor in key.split(", "):
            descriptors[descriptor] = package
            package_name = descriptor_name(descriptor)
            if package_name:
                packages_by_name.setdefault(package_name, []).append(package)

    provides = set()
    queue = [
        (root_package.get("name", ""), name, reference)
        for name, reference in dependencies(root_package)
    ]
    visited_descriptors = set()
    visited_workspaces = set()
    resolutions = root_package.get("resolutions", {})
    while queue:
        parent, name, reference = queue.pop()
        if reference.startswith("workspace:"):
            if name in visited_workspaces:
                continue
            visited_workspaces.add(name)
            workspace = workspaces.get(name)
            if workspace is None:
                raise ValueError(f"Yarn workspace not found: {name}")
            queue.extend(
                (name, child, child_reference)
                for child, child_reference in dependencies(workspace)
            )
            continue

        overrides = [
            value
            for pattern, value in resolutions.items()
            if pattern in (name, f"{name}@{reference}", f"{name}@npm:{reference}")
            or pattern == f"{parent}/{name}"
        ]
        if len(set(overrides)) == 1:
            reference = overrides[0]

        descriptor = f"{name}@{reference}"
        package = descriptors.get(descriptor)
        if package is None and not reference.startswith(("npm:", "patch:", "portal:", "file:")):
            descriptor = f"{name}@npm:{reference}"
            package = descriptors.get(descriptor)
        if package is None:
            locator_matches = [
                candidate
                for candidate in descriptors
                if candidate.startswith(f"{descriptor}::")
            ]
            if len(locator_matches) == 1:
                package = descriptors[locator_matches[0]]
        if package is None:
            candidates = packages_by_name.get(name, [])
            resolutions_for_name = {candidate.get("resolution") for candidate in candidates}
            if len(resolutions_for_name) == 1:
                package = candidates[0]
        if package is None:
            raise ValueError(f"Yarn descriptor not found: {descriptor}")
        if descriptor in visited_descriptors:
            continue
        visited_descriptors.add(descriptor)

        resolution = package.get("resolution", "")
        resolved_name = descriptor_name(resolution) or name
        version = package.get("version")
        if version:
            provides.add((resolved_name, str(version)))
        queue.extend(
            (resolved_name, child, child_reference)
            for child, child_reference in dependencies(package)
        )
    return provides


def read_member(source, member):
    extracted = source.extractfile(member)
    if extracted is None:
        raise ValueError(f"could not read {member.name}")
    return extracted.read().decode()


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", sys.argv[1]):
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} VERSION")
    version = sys.argv[1]
    package_dir = Path(__file__).resolve().parent
    archive = package_dir / f"grafana-{version}.tar.gz"
    specs = list(package_dir.glob("grafana*.spec"))
    if len(specs) != 1:
        raise SystemExit("expected exactly one grafana*.spec")

    with tarfile.open(archive, "r:gz") as source:
        prefix = f"grafana-{version}/"
        lock = read_member(source, source.getmember(f"{prefix}yarn.lock"))
        root_package = json.loads(read_member(source, source.getmember(f"{prefix}package.json")))
        patterns = root_package.get("workspaces", {}).get("packages", [])
        workspaces = {}
        for member in source.getmembers():
            relative = member.name.removeprefix(prefix)
            if not any(Path(relative).match(f"{pattern}/package.json") for pattern in patterns):
                continue
            package = json.loads(read_member(source, member))
            if package.get("name"):
                workspaces[package["name"]] = package

    provides = yarn_packages(lock, root_package, workspaces)

    generated = "\n".join(
        f"Provides:       bundled(npm({name})) = {rpm_version(package_version)}"
        for name, package_version in sorted(provides)
    )
    spec = specs[0]
    text = spec.read_text()
    pattern = re.compile(rf"(?<={re.escape(BEGIN)}\n).*?(?={re.escape(END)})", re.DOTALL)
    updated, count = pattern.subn(generated + "\n", text)
    if count != 1:
        raise SystemExit(f"{spec}: expected exactly one generated block")
    spec.write_text(updated)
    print(f"wrote {len(provides)} bundled npm Provides to {spec.name}")


if __name__ == "__main__":
    main()
