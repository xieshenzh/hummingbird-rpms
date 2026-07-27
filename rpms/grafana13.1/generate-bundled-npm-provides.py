#!/usr/bin/python3
"""Refresh bundled npm Provides from Grafana's Yarn lockfile."""

import re
import sys
import tarfile
from pathlib import Path

BEGIN = "# BEGIN generated bundled npm Provides"
END = "# END generated bundled npm Provides"
RESOLUTION_RE = re.compile(r'^  resolution: "(.+)@npm:[^"]+"$')
VERSION_RE = re.compile(r'^  version: "?([^"\n]+)"?$')


def rpm_version(version):
    """Translate npm prerelease syntax to an RPM-compatible EVR."""
    base, separator, prerelease = version.partition("-")
    if separator:
        prerelease = prerelease.lstrip("-._+~").replace("-", ".")
        version = base + "~" + prerelease
    return re.sub(r"([._+~])\1+", r"\1", version)


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
        member = source.getmember(f"grafana-{version}/yarn.lock")
        extracted = source.extractfile(member)
        if extracted is None:
            raise SystemExit(f"{archive}: missing yarn.lock")
        lock = extracted.read().decode()

    provides = set()
    for block in re.split(r"(?=^\S.*:\n)", lock, flags=re.MULTILINE):
        name = package_version = None
        for line in block.splitlines():
            if match := RESOLUTION_RE.fullmatch(line):
                name = match.group(1)
            elif match := VERSION_RE.fullmatch(line):
                package_version = match.group(1)
        if name and package_version:
            provides.add((name, package_version))

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
