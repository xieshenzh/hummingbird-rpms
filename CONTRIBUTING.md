# Contributing to Hummingbird RPMs

Thank you for your interest in contributing! This repository contains RPM packaging, minimal tests,
and CI plumbing used by Hummingbird to build, lint, and validate RPMs in containers and Testing
Farm.

This guide is adapted from the Hummingbird containers contribution guide and aligned with our
workflows and tooling. See the original for broader context:
[Hummingbird Containers CONTRIBUTING](https://gitlab.com/redhat/hummingbird/containers/-/blob/main/CONTRIBUTING.md).

## Code of Conduct

Be respectful and constructive. By participating, you agree to uphold a professional and inclusive
environment.

## Repository layout

- `rpms/<package>/` – RPM dist-gits (spec, sources), mostly auto-imported
- `ci/` – helper scripts and default tests
  - `build_rpms.sh` – build RPMs using Mock in the Konflux-compatible container
  - `run_tests_rpm.sh` – run rpmlint and install/rebuild tests in a container
  - `default-tests/tests-rpm.yml` – default tests included for all packages
  - `run_tests_rpm.fmf` – tmt/Testing Farm entry point
- `konflux-templates/` – Konflux/PAC resources
- `mock/` – mock configuration
- `test/rpms/<package>.yml` – package-specific tests

## Prerequisites

- Fedora or RHEL-like environment
- Podman
- rpmlint
- tmt (optional, for local TF-style runs)
- jq, python3, python3-yaml (used by `run_tests_rpm.sh`)

## Build locally

Build a package's SRPM and RPMs using the Konflux-aligned environment:

```bash
./ci/build_rpms.sh <package_name>
# Results are written to: /tmp/konflux-build-<package_name>-*/results/
```

### Building in Lima VM

When building inside a Lima VM (macOS users), use the `--build-dir` flag to specify a directory on
the VM's native filesystem. This avoids permission issues with mock's bootstrap process on macOS
mounts:

```bash
limactl shell fedora bash -c 'cd /path/to/repo && ./ci/build_rpms.sh --build-dir /tmp/rpm-build <package_name>'
```

The build directory must be on the VM's native filesystem (not a macOS mount) to ensure Linux file
ownership, permissions, and symlinks work correctly with `/var/lib/mock`.

### Interactive repository debugging

To investigate package/dependency/installability issues, you can run an interactive shell
in the same environment used by the package builds:

```bash
./ci/build_rpms.sh --shell-before setup
```

This will show you the `mock` command that would be used to build the `setup` package, and drop you
into a shell in the same environment before executing the build, with `dnf` available. You can then
run the command manually and inspect the environment, repositories, and package metadata.

If you want it to actually build the local package first, so that you can include it in your
investigation, use `--shell-after` instead:

```bash
./ci/build_rpms.sh --shell-after setup
```

## Test locally (containerized)

Run the default and package-specific tests against one or more built RPMs:

```bash
# Basic usage (binary rpm)
./ci/run_tests_rpm.sh --rpm /path/to/pkg-1.2-1.fcXX.x86_64.rpm <package_name>

# Include source RPM tests
./ci/run_tests_rpm.sh \
  --rpm /path/to/pkg-1.2-1.fcXX.x86_64.rpm \
  --src-rpm /path/to/pkg-1.2-1.fcXX.src.rpm \
  <package_name>

# Test all RPMs in the build output directory after build_rpms.sh
./ci/run_tests_rpm.sh $(printf -- '--rpm %s ' builds/<package_name>/RPMS/*.rpm) \
  --repo-dir builds/<package_name>/RPMS/ \
  --src-rpm builds/<package_name>/SRPMS/*.src.rpm \
  <package_name>
```

Notes:

- Tests run inside a Podman container.
- The test image defaults to `quay.io/hummingbird/core-runtime:latest-builder`. You can override via
  `TEST_IMAGE`:

```bash
TEST_IMAGE=quay.io/hummingbird/core-runtime:specific-tag ./ci/run_tests_rpm.sh --rpm /path/to/pkg.rpm <package_name>
```

- Container execution is performed as root with `HOME=/root` to avoid XDG state permission issues in
  dnf5.

## Test in tmt/Testing Farm locally

You can drive the same FMF test locally with tmt. The FMF test expects an OCI artifact that contains
RPMs, referenced via `IMAGE_URL` (Testing Farm sets this automatically). For local trials you can
point to any compatible OCI artifact or skip the ORAS pull logic and directly call the script.

```bash
# If using an OCI artifact with RPMs
IMAGE_URL="oci://registry/namespace/artifact:tag_or_digest" tmt run -a

# Or run the script directly with built RPMs (bypassing the FMF wrapper)
./ci/run_tests_rpm.sh --rpm /path/to/pkg.rpm --src-rpm /path/to/pkg.src.rpm <package_name>
```

If you need longer time in Testing Farm, the FMF test includes `duration`, which you can adjust in
`ci/run_tests_rpm.fmf`.

## Dist-git Imports

The `ci/dist_git.py` tool imports Fedora/CentOS dist-git packages into the `rpms/` directory. We
expect most packages to not have (permanent) Hummingbird specific changes, so most of them will keep
syncing with upstream dist-gits. In most cases that will be Fedora rawhide, but for some packages we
may pick a different upstream, e.g. stable Fedora or even CentOS Stream.

The status of all imports is tracked in [imports.json](./imports.json). Active Fedora releases are
tracked in [upstream-releases.json](./upstream-releases.json), which is updated from the Bodhi API.
Note that `rawhide` is automatically resolved to the highest numbered Fedora release at runtime and
is not stored in the JSON file.

See `./ci/dist_git.py --help` for all available options. Some examples:

- Update upstream-releases.json from Bodhi API (should be done periodically, e.g., when new Fedora
  versions are released):

```bash
./ci/dist_git.py update-releases
```

This fetches the latest Fedora releases from Bodhi. The `rawhide` branch automatically resolves to
the highest numbered Fedora version (e.g., if f43 and f44 are available, rawhide uses f44).

- Import a new package. This requires specifying the dist-git URL (with `fedora/` being a shortcut
  for the Fedora dist-git URL) and optionally a branch (default: rawhide):

```bash
./ci/dist_git.py import fedora/bash

./ci/dist_git.py import --branch f42 fedora/glibc
./ci/dist_git.py import --branch c10s https://gitlab.com/redhat/centos-stream/rpms/postfix.git
```

- Update all or a single package:

```bash
./ci/dist_git.py update

./ci/dist_git.py update bash
```

This only imports changes if these were actually built in Koji, to ensure we only import changes
which are meant to be released. You can disable this check with `--skip-build-check`.

- Re-sync a package to upstream, discarding any local modifications. We use this after Fedora
  adopted our change, or it is no longer relevant:

```bash
./ci/dist_git.py sync bash
```

All of these commands automatically commit changes with descriptive commit messages including the
upstream SHA. To avoid that, you can use the `--dry-run` option.

- Enable upstream version tracking for a package (used by `check_upstream_versions.py check`). These
  commands modify the metadata file but do not create a git commit:

```bash
./ci/dist_git.py set-upstream bash --track
./ci/dist_git.py set-upstream bash --no-track
```

- Check upstream version status. The `check` subcommand checks tracked packages; `list` shows all
  packages in a table:

```bash
./ci/check_upstream_versions.py check
./ci/check_upstream_versions.py list
./ci/check_upstream_versions.py list --json
```

## Package-specific overrides

Per-package build configuration can be customized in `ci/package-overrides.yaml`. If a package is
not listed, it uses default settings.

Available options:

| Option            | Description                                             | Default           |
| ----------------- | ------------------------------------------------------- | ----------------- |
| `timeout_hours`   | Build timeout in hours                                  | 4                 |
| `build_platforms` | List of MPLs (instance sizes) for multi-platform builds | Pipeline defaults |

Example configuration:

```yaml
# Long-running package with custom timeout
setup:
  timeout_hours: 12

# Package requiring larger build instances
llvm:
  timeout_hours: 12
  build_platforms:
    - "linux-d160-c8xlarge/arm64"
    - "linux-d160-c8xlarge/amd64"
```

All available MPLs can be found in the
[Konflux multi-platform builds documentation](https://konflux.pages.redhat.com/docs/users/getting-started/multi-platform-builds.html).

After modifying overrides, regenerate the pipeline files:

```bash
make generate
```

This updates `.tekton/rpms-on-push.yaml` and `.tekton/rpms-on-pull-request.yaml` with the new
configuration.

## Branching and pull requests

- Create feature branches from the default branch.
- Keep edits focused and small; separate unrelated changes into separate PRs.
- Include meaningful commit messages (why + what). Reference related issues if applicable.
- PRs must pass CI (build + tests). Fix lint/test failures or mark known failures properly.

## Commit message conventions

- First line: short imperative summary (≤ 72 chars)
- Body (optional): context, rationale, and user/ops impact
- Reference issues using standard notation (e.g. "Fixes: #123")

## Spec guidelines

- Don't use `%changelog` for Hummingbird specific changes. It's just a point of
  conflict with Fedora imports, commit messages are good enough.
- Hummingbird specific changes increase `Release:` number in steps of 0.1 (e.g.
  2 → 2.1 → 2.2). This avoids colliding with Fedora's Release number namespace
  and allows us to sync again to a future -3.

## Packaging and testing guidelines

- Spec files should be reproducible and minimal.
- Prefer pinned container digests for CI images where feasible. If a tag and digest are both present
  (`name:tag@sha256:<digest>`), the digest is authoritative for content selection.
- Default tests in `ci/default-tests/tests-rpm.yml` must be fast, deterministic, and safe for all
  packages.
- Package-specific tests (`rpms/<package>/tests-rpm.yml`) can override or extend defaults. Keep them
  bounded in runtime and dependencies.
- When possible, capture flaky or environmental issues under `known_issues` in test YAML with clear
  matching patterns and descriptions.

## Style

- Shell: bash with `set -euo pipefail`, readable variable names, and early returns where reasonable.
- YAML: consistent indentation and quoting; prefer explicitness over magic.
- Comments: add when necessary to explain non-obvious rationale; avoid restating the code.

## Security and supply chain

- Avoid embedding credentials or secrets; use environment variables or CI secret stores.
- Prefer pulling images by digest for stability and repeatability in CI.
- Validate inputs and sanitize any paths used by scripts.

## Resolving CVEs

When *manually* fixing a CVE (e.g., adding a patch or performing a manual
update), a `Fixes: CVE-YYYY-XXXX` must be added to the MR description
and/or a commit.  This will indicate to the build system that a CVE fix has
been submitted for review.

## Reporting issues

Open an issue describing the problem, reproduction steps, and environment. Attach logs (build/test)
when possible. If the failure is intermittent, call that out explicitly.

## Licensing

Ensure files include appropriate licenses and that any third-party content is compatible with the
project's license.

## Thank you

Your contributions make the Hummingbird RPMs better for everyone. We appreciate your time and
feedback!
