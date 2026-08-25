#!/usr/bin/python3
"""Refresh bundled Maven Provides from an assembled Maven distribution."""

import hashlib
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path


BEGIN = "# BEGIN generated bundled Maven Provides"
END = "# END generated bundled Maven Provides"
PROPERTY_RE = re.compile(r"^\s*([^#!:=\s]+)\s*[:=]\s*(.*?)\s*$")


def pom_properties(jar: bytes) -> set[tuple[str, str, str]]:
    coordinates = set()
    with zipfile.ZipFile(io.BytesIO(jar)) as archive:
        properties = [
            name
            for name in archive.namelist()
            if name.startswith("META-INF/maven/") and name.endswith("/pom.properties")
        ]
        for name in properties:
            values = {}
            for line in archive.read(name).decode().splitlines():
                if match := PROPERTY_RE.match(line):
                    values[match.group(1)] = match.group(2)
            required = ("groupId", "artifactId", "version")
            if all(values.get(key) for key in required):
                coordinates.add(tuple(values[key] for key in required))
    return coordinates


def repository_jars(repository: Path) -> dict[bytes, set[tuple[str, str, str]]]:
    jars = {}
    for jar in repository.rglob("*.jar"):
        relative = jar.relative_to(repository)
        if len(relative.parts) < 4:
            continue
        artifact = relative.parts[-3]
        version = relative.parts[-2]
        group = ".".join(relative.parts[:-3])
        if not jar.name.startswith(f"{artifact}-{version}"):
            continue
        digest = hashlib.sha256(jar.read_bytes()).digest()
        jars.setdefault(digest, set()).add((group, artifact, version))
    return jars


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            f"usage: {Path(sys.argv[0]).name} BINARY_TARBALL MAVEN_REPOSITORY"
        )
    binary = Path(sys.argv[1])
    repository = Path(sys.argv[2])
    if not binary.is_file():
        raise SystemExit(f"{binary}: binary tarball not found")

    if not repository.is_dir():
        raise SystemExit(f"{repository}: Maven repository not found")

    repository_coordinates = repository_jars(repository)
    provides = set()
    with tarfile.open(binary, "r:gz") as distribution:
        members = [
            member
            for member in distribution.getmembers()
            if member.isfile()
            and re.search(r"/(?:boot|lib)/[^/]+\.jar$", member.name)
        ]
        for member in members:
            extracted = distribution.extractfile(member)
            if extracted is None:
                raise ValueError(f"could not read {member.name}")
            jar = extracted.read()
            coordinates = repository_coordinates.get(hashlib.sha256(jar).digest(), set())
            if not coordinates:
                coordinates = pom_properties(jar)
            if not coordinates:
                raise ValueError(f"no Maven coordinates in {member.name}")
            provides.update(
                coordinate for coordinate in coordinates if coordinate[0] != "org.apache.maven"
            )
    if not provides:
        raise SystemExit(f"{binary}: no bundled Maven dependencies found")

    generated = "\n".join(
        f"Provides: bundled(mvn({group}:{artifact})) = {version}"
        for group, artifact, version in sorted(provides)
    )
    package_dir = Path(__file__).resolve().parent
    specs = list(package_dir.glob("*.spec"))
    if len(specs) != 1:
        raise SystemExit("expected exactly one spec file")
    spec = specs[0]
    text = spec.read_text()
    pattern = re.compile(rf"(?<={re.escape(BEGIN)}\n).*?(?={re.escape(END)})", re.DOTALL)
    updated, count = pattern.subn(generated + "\n", text)
    if count != 1:
        raise SystemExit(f"{spec}: expected exactly one generated block")
    spec.write_text(updated)
    print(f"wrote {len(provides)} bundled Maven Provides to {spec.name}")


if __name__ == "__main__":
    main()
