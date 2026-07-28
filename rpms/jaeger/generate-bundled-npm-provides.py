#!/usr/bin/python3
"""Refresh bundled npm Provides from the Jaeger UI pnpm lockfile."""

import ast
import json
import re
import sys
import tarfile
from pathlib import Path

import yaml

BEGIN = "# BEGIN generated bundled npm Provides"
END = "# END generated bundled npm Provides"
PACKAGE_KEY_RE = re.compile(r"^  (\S.*):$")
PACKAGE_NAME_RE = re.compile(r"^(?:@[^/@]+/)?[^/@]+$")


def rpm_version(version: str) -> str:
    """Translate npm prerelease syntax to an RPM-compatible EVR."""
    base, separator, prerelease = version.partition("-")
    if separator:
        prerelease = prerelease.lstrip("-._+~").replace("-", ".")
        version = base + "~" + prerelease
    return re.sub(r"([._+~])\1+", r"\1", version)


def unquote(value: str) -> str:
    if value.startswith(("'", '"')):
        parsed = ast.literal_eval(value)
        if not isinstance(parsed, str):
            raise ValueError(f"invalid pnpm package key: {value}")
        return parsed
    return value


def lockfile_provides(lock: str) -> set[tuple[str, str]]:
    """Return production dependency closure from pnpm's importers and snapshots."""
    data = yaml.safe_load(lock)
    snapshots = data.get("snapshots", {})
    queue = []
    for importer in data.get("importers", {}).values():
        for section in ("dependencies", "optionalDependencies"):
            for name, dependency in importer.get(section, {}).items():
                if reference := pnpm_reference(name, dependency):
                    queue.append(reference)

    visited = set()
    provides = set()
    while queue:
        key = queue.pop()
        if key in visited:
            continue
        visited.add(key)
        package_key = key.split("(", 1)[0]
        name, separator, version = package_key.rpartition("@")
        if not separator or not PACKAGE_NAME_RE.fullmatch(name) or not version:
            raise ValueError(f"unsupported pnpm package key: {key}")
        provides.add((name, version))
        snapshot = snapshots.get(key)
        if snapshot is None:
            raise ValueError(f"pnpm snapshot not found: {key}")
        for section in ("dependencies", "optionalDependencies"):
            for dependency_name, dependency in snapshot.get(section, {}).items():
                if reference := pnpm_reference(dependency_name, dependency):
                    queue.append(reference)
    if not provides:
        raise ValueError("pnpm production dependency closure is empty")
    return provides


def pnpm_reference(name: str, dependency: object) -> str | None:
    reference = dependency.get("version") if isinstance(dependency, dict) else dependency
    if not isinstance(reference, str) or reference.startswith(("link:", "workspace:", "file:")):
        return None
    if reference.startswith("npm:"):
        alias, separator, version = reference[4:].rpartition("@")
        if not separator:
            raise ValueError(f"unsupported pnpm alias: {reference}")
        return f"{alias}@{version}"
    return f"{name}@{reference}"


def lockfile_importers(lock: str) -> set[str]:
    """Return non-root workspace paths from pnpm's importers section."""
    importers = False
    paths = set()
    for line in lock.splitlines():
        if line == "importers:":
            importers = True
            continue
        if importers and line and not line.startswith(" "):
            break
        if not importers or not (match := PACKAGE_KEY_RE.fullmatch(line)):
            continue
        path = unquote(match.group(1))
        if path != ".":
            paths.add(path)
    return paths


def archive_member(source: tarfile.TarFile, suffix: str) -> tarfile.TarInfo:
    matches = [member for member in source.getmembers() if member.name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one archive member ending in {suffix}")
    return matches[0]


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    extracted = source.extractfile(member)
    if extracted is None:
        raise ValueError(f"could not read {member.name}")
    return extracted.read().decode()


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", sys.argv[1]):
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} VERSION")
    version = sys.argv[1]
    package_dir = Path(__file__).resolve().parent
    archive = package_dir / f"jaeger-ui-{version}.tar.gz"
    spec = package_dir / "jaeger.spec"

    with tarfile.open(archive, "r:gz") as source:
        lock = read_member(source, archive_member(source, "/pnpm-lock.yaml"))
        provides = lockfile_provides(lock)
        workspace_packages = []
        for importer in sorted(lockfile_importers(lock)):
            package = json.loads(
                read_member(source, archive_member(source, f"/{importer}/package.json"))
            )
            if package.get("name") and package.get("version"):
                workspace_packages.append((package["name"], package["version"]))

    generated = [
        f"Provides:       bundled(npm({name})) = {rpm_version(package_version)}"
        for name, package_version in sorted(provides)
    ]
    generated.extend(
        f"Provides:       bundled(npm({name})) = {rpm_version(package_version)}"
        for name, package_version in sorted(workspace_packages)
    )

    text = spec.read_text()
    pattern = re.compile(rf"(?<={re.escape(BEGIN)}\n).*?(?={re.escape(END)})", re.DOTALL)
    updated, count = pattern.subn("\n".join(generated) + "\n", text)
    if count != 1:
        raise SystemExit(f"{spec}: expected exactly one generated block")
    spec.write_text(updated)
    print(f"wrote {len(generated)} bundled npm Provides to {spec.name}")


if __name__ == "__main__":
    main()
