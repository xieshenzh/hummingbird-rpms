---
title: Source Pipeline Tool
status: Draft
authors: Scott Hebert
date: 2026-07-16
related:
  - "[HUM-793] Upstream Release Trust Verification"
  - "[HUM-4619] Independent Source Tarball Generation"
  - "[HUM-4610] Declarative Source Pipeline"
  - "[HUM-3299] Build RPM UI assets in Konflux"
---

# Source Pipeline Tool

## Problem Statement

RPM-based distributions that derive packages from upstream sources face a common set of supply chain
challenges:

1. **Supply chain trust gap.** Distributions that consume source tarballs from another distribution's
   lookaside cache (e.g., Fedora's) inherit an unverified trust boundary. Package maintainers
   download upstream release artifacts, may modify them, and upload to a lookaside cache. Downstream
   consumers use those tarballs without verifying they match upstream. Any maintainer could,
   intentionally or unintentionally, introduce modifications that compromise packages.

2. **Version dependency on upstream packagers.** Downstream distributions are blocked on upstream
   maintainers to package new releases. If upstream ships a critical fix, downstream must wait for
   the intermediate distribution to update before consuming it.

3. **Non-durable security transforms.** When a distribution applies a security fix to vendored
   dependencies (e.g., patching a lockfile to bump a vulnerable transitive dependency), a future
   upstream update regenerates vendor artifacts from the upstream baseline. The fix silently
   disappears. The update succeeds, CI passes, and the vulnerable dependency is back.

4. **Unverified patches.** Patches (`.patch` files) from upstream distributions flow into packages
   without verification. There is no check whether patches correspond to upstream commits, whether
   they are needed for the target build environment, or whether new patches have been introduced.

## Tool Overview

A **containerized pipeline tool** that reads a declarative per-package YAML definition, fetches
source tarballs directly from upstream, applies transforms, verifies integrity, enforces policies,
and emits artifacts ready for a lookaside cache.

```
                          ┌──────────────────────────────┐
                          │     source-pipeline tool     │
                          │      (container image)       │
                          │                              │
  ┌───────────┐           │  ┌───────┐    ┌───────────┐  │    ┌───────────────┐
  │  package  │           │  │ fetch │───▸│ transform │  │    │   tarballs    │
  │   dir     │──────────▸│  └───────┘    └─────┬─────┘  │───▸│   sources     │
  │ (spec,    │           │                     │        │    │   report      │
  │  patches, │           │  ┌───────────┐      │        │    └───────┬───────┘
  │  config)  │           │  │  verify   │◂─────┘        │            │
  │           │           │  └─────┬─────┘               │            ▼
  └───────────┘           │        │                     │    ┌───────────────┐
                          │  ┌─────▼─────┐               │    │   lookaside   │
  ┌───────────┐           │  │  enforce  │               │    │    cache      │
  │ pipeline  │           │  │  policy   │               │    └───────────────┘
  │   yaml    │──────────▸│  └─────┬─────┘               │
  │           │           │        │                     │
  └───────────┘           │  ┌─────▼─────┐               │
                          │  │   emit    │               │
                          │  └───────────┘               │
                          └──────────────────────────────┘
```

### Design principles

- **Runs anywhere.** Same container image via podman — locally for development, in CI for
  automation, in SLSA-attested build environments for provenance.
- **Declarative.** Per-package behavior is defined in YAML, not imperative scripts. The YAML serves
  as an attestation artifact — anyone can read it to understand exactly how a package's sources are
  produced.
- **Fail-closed.** Verification failures and policy violations fail the pipeline. No silent
  fallbacks. On failure, the package is skipped (stays at its current version) and the automation
  continues to the next package.
- **Ecosystem-aware.** Built-in support for Go (vendor archives), npm (node_modules), cargo
  (vendor), and Composer (PHP) workflows, with escape hatches for custom transforms.

### Container interface

```
podman run --rm \
  -v ./<package-dir>:/package:ro \
  -v ./pipeline.yaml:/pipeline.yaml:ro \
  -v ./gpg-keys:/gpg-keys:ro \
  -v ./output:/output \
  source-pipeline:latest \
  --version <new-version> \
  [--old-version <old-version>] \
  [--verify-patches /patches-from-upstream]
```

**Inputs (mounted read-only):**
- `/package` — the package directory (spec file, patches, existing `sources` file)
- `/pipeline.yaml` — the declarative pipeline definition
- `/gpg-keys` — centralized GPG keyring directory

**Outputs (written to `/output`):**
- Source tarballs (ready for lookaside upload)
- `sources` — updated sources manifest with checksums
- `report.json` — verification and policy results (pass/fail per check, patch classifications)

**Exit codes:**
- `0` — success, all checks passed
- `1` — error (download failure, tool error)
- `2` — policy violation (verification or constraint failure)

On any non-zero exit, `report.json` is still written with the failure details (stage, error type,
message). The calling automation uses this to log the failure and skip the package — no commit is
created, the package stays at its current version, and the automation continues to the next
package. Transient failures (exit 1) self-heal on the next scheduled run; verification and policy
failures (exit 2) require human intervention.

## Pipeline Stages

### 1. Fetch

Downloads source artifacts directly from upstream.

- Parses `Source:` URLs from the spec file (resolving RPM macros like `%{version}`, `%{name}`,
  `%{url}`)
- Downloads each source from the upstream URL
- Supports fetching from git repositories at a tag, branch, or commit
- For packages without a pipeline YAML, uses a default fetch-from-spec behavior (covering the
  "trivial" package case)

### 2. Transform

Applies per-package source modifications.

- **Vendor archive generation** — runs ecosystem-specific tooling (e.g., `go_vendor_archive` for
  Go, `npm pack` for Node.js, `cargo vendor` for Rust)
- **Vendor dependency pinning** — modifies lockfiles (`go.mod`/`go.sum`, `package-lock.json`,
  `Cargo.lock`) to bump specific dependencies to required versions before vendoring. This is the
  enforcement counterpart to policy's validation: pins are applied during transform, then policy
  confirms the result. Solves the non-durable security transforms problem — a declarative pin is
  re-applied on every source generation, so upstream updates cannot silently revert a CVE fix.
- **Source stripping** — removes content that cannot be distributed (crypto, bundled pre-built
  binaries, non-free assets)
- **UI asset builds** — builds JavaScript/TypeScript UI assets from source (for packages like
  Prometheus, Jaeger, Grafana that currently vendor pre-built UI)
- **Custom transforms** — escape hatch for arbitrary commands when built-in stages are insufficient

### 3. Verify

Validates integrity and authenticity of fetched sources.

- **GPG signature verification** — downloads signature files (`.asc`, `.sig`) declared in the spec
  and verifies against upstream keys stored in a centralized keyring directory. Keys are organized
  by upstream project (e.g., `gpg-keys/curl.gpg`). Centralized storage means the full set of
  trusted keys is auditable in one directory, and key rotation or revocation is a single-commit
  operation.
- **Checksum verification** — compares against published checksums where available
- **Reproducibility check** — for packages with existing tarballs from another distribution,
  optionally compares the independently-fetched tarball to identify divergences
- **Re-publication detection** — if a previously committed checksum exists in the `sources` file
  for the same version and the freshly downloaded artifact does not match, the pipeline fails
  (exit code 2). This catches upstream projects that silently re-publish release artifacts under
  the same version. The pipeline will continue to fail on automated retries until a human
  explicitly updates an `accepted-checksums` entry in the pipeline YAML with the new hash and a
  reason. This forces investigation, prevents automated retries from silently accepting changed
  content, and provides a committed audit trail of what changed and why.

### 4. Enforce policy

Validates the final artifacts. Acts as a safety net for `vendor-pin` (confirms pins took effect)
and catches violations in packages that don't use `vendor-pin`.

- **Vendor dependency constraints** — ensures vendored dependencies meet version requirements
  (e.g., `sanitize-html >= 2.17.5` for a CVE fix)
- **Ecosystem-specific checks** — Go module verification, npm audit, cargo audit
- **License compliance** — flags vendored dependencies with incompatible licenses
- Fail-closed: any policy violation exits with code 2

### 5. Emit

Produces final artifacts.

- Writes tarballs to the output directory
- Generates `sources` file in dist-git format (`SHA512 (filename) = hash`)
- Writes `report.json` with verification results, policy check results, and patch classification

## Pipeline Schema

Per-package pipeline definitions are YAML files that declare the full source generation workflow.

Packages without a pipeline definition use a built-in default that fetches sources from the spec's
`Source:` URLs — no transforms, no verification, no policy.

### Schema definition

```yaml
# Spec preparation (optional)
# Runs before fetch — fixes version macros that the specfile library cannot
# trace, so Source URLs resolve correctly.
spec-update:
  # Macro substitutions applied before Source URL resolution
  macros:
    - name: go_patch                     # %global go_patch <new-value>
      value: "${VERSION_PATCH}"          # extracted from VERSION (e.g., 1.25.3 → 3)

    - name: k8s_ver                      # %global k8s_ver <new-value>
      value: "${VERSION}"

  # Reset Release to 0.1%{?dist} on version bump (common for versioned packages)
  reset-release: true

# Source fetching
fetch:
  sources:
    # Fetch Source0 from the URL declared in the spec
    - spec-source: 0

    # Fetch Source1 (e.g., a GPG signature file)
    - spec-source: 1

    # Or fetch from an explicit URL (for sources not in the spec)
    - url: "https://example.com/extra-source-${VERSION}.tar.gz"

    # Or fetch from a git repository at a tag/commit
    - git:
        repo: "https://github.com/example/project"
        ref: "v${VERSION}"              # tag, branch, or commit hash
        include-history: false           # include .git dir in tarball (default: false)

  # Vendor archive generation (optional)
  vendor:
    ecosystem: go                        # go | npm | cargo | composer
    config: go-vendor-tools.toml         # ecosystem-specific config file in the package dir
    source-dir: .                        # directory to vendor from (default: extracted source root)
    submodules:                          # (optional) vendor multiple Go submodules independently
      - server
      - etcdctl
      - etcdutl

# Source transforms (optional, ordered)
transform:
  # Built-in transform types
  - strip-tarball:
      source: "node-v${VERSION}.tar.gz"
      remove:
        - "deps/openssl/"
        - "deps/ngtcp2/ngtcp2/crypto/"
      output: "node-v${VERSION}-stripped.tar.gz"

  # Pin vendored dependency versions before vendor archive generation.
  # Modifies lockfiles (go.mod/go.sum, package-lock.json, Cargo.lock) to
  # bump specific dependencies, then re-resolves the dependency graph.
  # Runs before the vendor stage so the pinned versions are included in
  # the vendor archive. Re-applied on every source generation, so upstream
  # updates cannot silently revert a security fix.
  - vendor-pin:
      - package: golang.org/x/crypto
        ecosystem: go
        version: ">= 0.31.0"
        reason: "CVE-2024-45337"

      - package: sanitize-html
        ecosystem: npm
        version: ">= 2.17.5"
        reason: "CVE-2024-XXXXX"

      - package: tokio
        ecosystem: cargo
        version: ">= 1.38.1"
        reason: "CVE-2024-YYYYY"

  # Custom command (escape hatch)
  - run: "./packaging/make-tarball.sh ${VERSION}"
    outputs:
      - "package-${VERSION}-stripped.tar.gz"

  # UI asset build
  - build-ui:
      ecosystem: npm                     # npm | yarn
      source-dir: "web/ui"
      output: "${PACKAGE}-${VERSION}-ui.tar.gz"

# Integrity verification (optional)
verify:
  gpg:
    signature-source: 1                  # spec Source index containing the .asc/.sig
    keyring: "curl.gpg"                  # key name in the GPG keys directory
  checksums:
    url: "https://example.com/SHA256SUMS"
    algorithm: sha256

  # Override for upstream re-publications. Required when upstream re-publishes
  # a release artifact with different content under the same version. The
  # pipeline refuses to accept changed content automatically — a human must
  # add the new checksum here after investigation.
  accepted-checksums:
    - file: "example-1.2.3.tar.gz"
      sha512: "abc123..."
      reason: "Upstream re-published with corrected LICENSE file (verified via upstream issue #456)"

# Policy enforcement (optional)
# Validates the final artifacts. vendor-constraints acts as a safety net:
# if vendor-pin (above) is used, policy confirms the pin took effect; if
# vendor-pin is not used, policy catches violations that need manual action.
policy:
  vendor-constraints:
    - package: sanitize-html
      ecosystem: npm
      version: ">= 2.17.5"
      reason: "CVE-2024-XXXXX"

    - package: golang.org/x/crypto
      ecosystem: go
      version: ">= 0.31.0"
      reason: "CVE-2024-45337"

# Patch verification (optional)
patches:
  verify: true                           # diff-detect new/changed patches
  classify: true                         # attempt to classify by origin
  upstream-repo: "https://github.com/curl/curl"
  fail-on-unverified: false              # warn-only by default

  # Patch lifecycle rules (optional)
  # Declares version-scoped applicability for patches. The pipeline tool reads
  # these rules from patch headers (preferred) or from this YAML, and enforces
  # them during updates: patches outside their valid range are flagged or dropped.
  lifecycle:
    - file: "fix-memory-leak.patch"
      applies-to: "< 1.5.0"             # drop this patch at version 1.5.0+
      reason: "Fixed upstream in 1.5.0 (commit abc123)"
      action: drop                       # drop | warn (default: warn)

    - file: "cve-2024-45337.patch"
      applies-to: "< 0.31.0"
      reason: "CVE-2024-45337 — fixed upstream in golang.org/x/crypto 0.31.0"
      action: drop

    - file: "distro-branding.patch"
      applies-to: "*"                    # carry forward unconditionally
      reason: "Distribution-specific branding, always required"

# Post-update spec modifications (optional)
# Runs after fetch/transform — extracts metadata from downloaded sources and
# patches it into the spec.
post:
  # Extract bundled dependency versions and splice into spec between markers
  - bundled-provides:
      modules-txt: "vendor/modules.txt"  # Go modules.txt path inside extracted source
      start-marker: "# --- bundled-deps.sh ---"
      end-marker: "# --- end bundled-deps.sh ---"

  # Run a custom command (escape hatch for complex metadata extraction)
  - run: "./packaging/fill-versions.sh ${SPEC_FILE} source-v${VERSION}-stripped.tar.gz"
```

### Variable substitution

The following variables are available in all string values:

| Variable | Value |
|----------|-------|
| `${VERSION}` | New upstream version (e.g., `1.25.3`) |
| `${VERSION_MAJOR}` | Major version component (e.g., `1`) |
| `${VERSION_MINOR}` | Minor version component (e.g., `25`) |
| `${VERSION_PATCH}` | Patch version component (e.g., `3`) |
| `${OLD_VERSION}` | Previous version |
| `${PACKAGE}` | Package name (directory name) |
| `${SPEC_FILE}` | Path to the spec file |

### Default behavior (no pipeline YAML)

When no pipeline YAML is provided, the tool applies a built-in default:

1. Parse all `Source:` URLs from the spec
2. Download each from the upstream URL
3. Generate `sources` file with SHA512 checksums
4. No transforms, no verification, no policy enforcement

This covers the common case where the upstream distribution's tarball is identical to upstream's
release artifact.

## Patch Verification

When packages are updated from an upstream distribution, patch files (`.patch`) may arrive without
inspection. The pipeline tool can optionally verify and manage patches.

### Verification approach

The pipeline tool can optionally verify patches when invoked with `--verify-patches`:

1. **Diff detection.** Compare the set of `.patch` files in the updated package directory against
   the previous version. Identify new patches, removed patches, and modified patches.

2. **Header parsing.** Extract metadata from patch headers:
   - `From:` — author identity
   - `Subject:` — description
   - Commit hash references (e.g., `From <hash>`, `cherry picked from commit <hash>`)
   - `Bug:` / `CVE:` references

3. **Classification.** Attempt to classify each patch:
   - **Upstream backport** — references a commit hash that exists in the upstream repo
   - **CVE fix** — references a CVE identifier
   - **Build/packaging fix** — modifies build system files (Makefile, configure, CMakeLists)
   - **Distribution-specific** — modifies paths, branding, or distribution-specific integration
   - **Unclassified** — cannot be automatically categorized

4. **Upstream verification.** For patches claiming to be backports, check whether the referenced
   commit exists in the upstream repo (via `git ls-remote` or the upstream API).

5. **Reporting.** Output classification and verification results in `report.json`. Optionally fail
   on unverified patches (controlled by `patches.fail-on-unverified` in the pipeline YAML).

### Patch lifecycle enforcement

Patches have version-scoped lifetimes. A CVE backport is only valid until the upstream version that
includes the fix. A build system workaround may only apply to a specific major version. Without
enforcement, a patch that should have been dropped at version 2.0 silently persists, and a patch
that must be carried forward can be accidentally removed during an update.

The pipeline tool enforces patch lifecycle rules declared either in patch headers or in the pipeline
YAML's `patches.lifecycle` section.

**Header-based declaration (preferred).** Patch authors add structured keywords to the patch header:

```
From: maintainer@example.com
Subject: Backport fix for CVE-2024-45337
Applies-To: < 0.31.0
Lifecycle-Action: drop
Lifecycle-Reason: Fixed upstream in golang.org/x/crypto 0.31.0
---
```

**YAML-based declaration (fallback).** For patches from upstream distributions that cannot have
headers modified, rules are declared in `patches.lifecycle` in the pipeline YAML (see schema above).
Header-based rules take precedence when both exist for the same patch.

**Enforcement behavior:**

1. During an update to version `${VERSION}`, the tool evaluates each patch's `applies-to` range
   against the new version.
2. If a patch is outside its valid range:
   - `action: drop` — the patch file is deleted from the package directory and the corresponding
     `Patch:` declaration and `%patch` / `%autopatch` application directives are removed from the
     spec. If the patch is applied inside a conditional block (`%if`), the tool flags it for manual
     intervention instead of attempting removal. The tool reports all actions in `report.json`.
   - `action: warn` (default) — the patch is flagged in `report.json` but not removed. The update
     proceeds.
3. If a patch has `applies-to: *`, it is always carried forward.
4. Patches without any lifecycle declaration are treated as having no version constraint (equivalent
   to `applies-to: *`, `action: warn`).

**Carry-forward enforcement.** The inverse case is also important: some patches (branding,
distribution-specific integration) must never be dropped. If a patch marked `applies-to: *` is
missing after an update (e.g., removed by an upstream distribution sync), the tool flags it as an
error.

### Example report output

```json
{
  "patches": {
    "new": [
      {
        "file": "fix-memory-leak.patch",
        "classification": "upstream-backport",
        "upstream_commit": "abc123def456",
        "verified": true
      }
    ],
    "removed": ["old-workaround.patch"],
    "unchanged": ["distro-paths.patch"],
    "lifecycle": [
      {
        "file": "cve-2024-45337.patch",
        "applies_to": "< 0.31.0",
        "current_version": "0.31.0",
        "action": "drop",
        "result": "removed — version 0.31.0 is outside applies-to range"
      },
      {
        "file": "distro-branding.patch",
        "applies_to": "*",
        "action": "carry-forward",
        "result": "present"
      }
    ]
  }
}
```

## Examples

### Trivial package (curl)

No pipeline YAML needed. The default behavior fetches `Source0` from the URL in the spec. If you
want GPG verification:

```yaml
fetch:
  sources:
    - spec-source: 0
    - spec-source: 1

verify:
  gpg:
    signature-source: 1
    keyring: "curl.gpg"
```

### Transformed package (Node.js)

Strip bundled OpenSSL, verify upstream checksums, extract component versions into spec:

```yaml
spec-update:
  macros:
    - name: nodejs_define_version node
      value: "${VERSION}"
  reset-release: true

fetch:
  sources:
    - url: "https://nodejs.org/dist/v${VERSION}/node-v${VERSION}.tar.gz"

transform:
  - strip-tarball:
      source: "node-v${VERSION}.tar.gz"
      remove:
        - "deps/openssl/"
      output: "node-v${VERSION}-stripped.tar.gz"

verify:
  checksums:
    url: "https://nodejs.org/dist/v${VERSION}/SHASUMS256.txt"
    algorithm: sha256

post:
  - run: "./packaging/fill-versions.sh ${SPEC_FILE} node-v${VERSION}-stripped.tar.gz"
```

### Simple Go vendor (caddy)

A pattern shared by nats-server, oauth2-proxy, and similar Go projects:

```yaml
fetch:
  sources:
    - git:
        repo: "https://github.com/caddyserver/caddy"
        ref: "v${VERSION}"

  vendor:
    ecosystem: go
```

### Multi-submodule Go vendor (etcd)

```yaml
fetch:
  sources:
    - url: "https://github.com/etcd-io/etcd/archive/v${VERSION}/etcd-${VERSION}.tar.gz"

  vendor:
    ecosystem: go
    config: go-vendor-tools.toml
    submodules:
      - server
      - etcdctl
      - etcdutl
```

### Code generation + Go vendor (opentelemetry-collector-contrib)

Built-in primitives handle fetch and vendor; the code generation step requires `run:`:

```yaml
fetch:
  sources:
    - url: "https://github.com/open-telemetry/opentelemetry-collector-releases/archive/v${VERSION}/opentelemetry-collector-releases-${VERSION}.tar.gz"

  vendor:
    ecosystem: go
    config: go-vendor-tools.toml
    source-dir: _build

transform:
  - run: |
      curl -fSL -o ocb "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/cmd%2Fbuilder%2Fv${VERSION}/ocb_${VERSION}_linux_amd64"
      chmod +x ocb
      tar -xzf "opentelemetry-collector-releases-${VERSION}.tar.gz"
      cd "opentelemetry-collector-releases-${VERSION}"
      if [ -f scripts/prepare-obi.sh ]; then
        bash scripts/prepare-obi.sh otelcol-contrib
      fi
      ../ocb --skip-compilation --config distributions/otelcol-contrib/manifest.yaml
    outputs:
      - "opentelemetry-collector-contrib-${VERSION}-generated.tar.bz2"
```

### Git snapshot (libXtst)

A pattern shared by libX11, libXext, libXi, libXrender:

```yaml
fetch:
  sources:
    - git:
        repo: "https://gitlab.freedesktop.org/xorg/lib/libXtst"
        ref: "libXtst-${VERSION}"
        include-history: true
```

## Built-in Primitives Coverage

An audit of 48 existing tarball/vendor scripts across 25+ packages identified the following
coverage:

**Fully declarative (no `run:` needed) — ~30 script files (15 unique patterns):**

| Built-in primitive | Packages covered |
|--------------------|------------------|
| `vendor: {ecosystem: go}` | caddy, nats-server×2, oauth2-proxy |
| `vendor: {ecosystem: go, submodules: [...]}` | etcd (3 submodules) |
| `strip-tarball: {remove: [...]}` | nodejs×5, cyrus-sasl, perl-libnet, java in-tree libs×2 |
| `fetch: {git: {repo, ref}}` | oniguruma, php-jsqueeze, libX11/Xext/Xi/Xrender/Xtst |
| `fetch` + `verify: {checksums}` | nodejs download+verify |
| `vendor: {ecosystem: composer}` | composer |

**Partially declarative (generic primitives + 1–2 `run:` steps):**

| Package | Generic part | Custom `run:` step |
|---------|-------------|-------------------|
| otel-collector, otel-collector-contrib | `vendor: {ecosystem: go}` | OCB binary code generation |
| java-openjdk×2 | `strip-tarball`, `fetch: {git}` | `./configure` + `make store-source-revision` (needs boot JDK) |
| selinux-policy | `fetch: {git}` (×3 repos) | Multi-repo selective archiving |

**Genuinely custom (`run:` required) — 7 packages:**

| Package | Why it can't be declarative |
|---------|----------------------------|
| nss-fips | Container-based RPM download with subscription-manager credentials + QEMU cross-arch |
| openssl-fips-provider | Container-based SRPM download + nested RPM extraction |
| ca-certificates | Interactive multi-source crypto trust data merging |
| gcc | GCC-specific changelog/PR extraction from git history |
| gdb | Interactive patch management tooling (stgit) |
| python3.14 | Koji task-specific JIT stencil artifact extraction |
| erlang27 | Complex patch reformatting + spec rewriting |

The `run:` escape hatch exists for these ~7 packages. All other packages should use built-in
primitives to maintain the declarative contract.

## Open Questions

1. **Implementation approach.** The pipeline YAML schema (ordered transform stages, `run:` escape
   hatches, variable substitution) resembles a bespoke Ansible without the ecosystem. The
   implementation choice also determines the language. Alternatives to consider:
   - **Custom tool (Python or Go)** — Python is consistent with existing tooling; Go produces a
     single static binary. Either way we own the full stack.
   - **Tekton StepActions** — already in some build ecosystems. Source generation could be a
     parameterized Tekton pipeline rather than a custom tool. Downside: harder to run locally.
   - **Shared shell function library** — the majority of scripts decompose into 3–4 operations
     (`vendor_go`, `strip_tarball`, `fetch_git`). A thin shell library called from per-package
     Makefiles may be more honest than YAML that serializes shell commands.
   - **Minimal declarative YAML + driver** — keep the YAML as a pure declaration of intent (what to
     fetch, what to strip, what to vendor) with no `run:` blocks, no ordering, no conditionals. A
     thin driver interprets it by calling shell functions. Packages that can't be expressed this way
     keep their shell scripts. This preserves the attestation value of the YAML without building a
     workflow engine.

---

# Hummingbird Integration

This section describes how the source pipeline tool integrates with Hummingbird's existing
infrastructure. The tool itself is distribution-agnostic; this section covers the
Hummingbird-specific wiring.

## Current Architecture

### Two update systems

| System | Packages | Source of tarballs | Hook system |
|--------|----------|--------------------|-------------|
| `dist_git.py update` | 450 (clean + modified) | Fedora lookaside cache (via `sources` file copied from Fedora dist-git) | None |
| `check_upstream_versions.py` | 22 (native) | Upstream URLs (via `download_sources` hooks or default spec URL download) | Yes — `update_spec`, `download_sources`, `post_update` |

**`dist_git.py update` flow:**

1. Clones Fedora dist-git for the package
2. Checks version, Koji build status, pre-release filtering
3. Copies entire Fedora dist-git checkout into `rpms/<package>/` via `shutil.copytree` — this
   includes the spec, patches, `.gitignore`, and `sources` file
4. For modified packages, performs a 3-way git merge to preserve local changes
5. Commits everything (spec, patches, `sources` file, metadata)

The `sources` file is committed to git as a text manifest (format: `SHA512 (filename) = hash`).
Actual tarballs are excluded by `.gitignore` and stored in a lookaside cache.

**`check_upstream_versions.py` flow:**

1. Queries release-monitoring.org for new upstream versions
2. Runs three hook phases per package:
   - `update_spec` — updates the spec's Version/Release (default: `specfile.update_version()`)
   - `download_sources` — downloads source tarballs (default: fetches from spec Source URLs)
   - `post_update` — additional steps (default: no-op)
3. Uploads downloaded tarballs to Hummingbird's lookaside cache
4. Updates the `sources` file with new checksums
5. Commits

Hooks are defined in `metadata/<package>.update-hooks.yaml`. 12 packages currently have hooks.

### Three lookaside cache backends

Configured in `mock/dist-git-client.ini`:

| Backend | URL pattern | Used by |
|---------|-------------|---------|
| Fedora | `src.fedoraproject.org/repo/pkgs/rpms/{name}/{filename}/{hashtype}/{hash}/{filename}` | ~410 packages (default) |
| CentOS Stream | `sources.stream.centos.org/sources/rpms/{name}/...` | 1 package (`rust-rpm-sequoia`) |
| Hummingbird | `d1766whheab9hg.cloudfront.net/rpms/{name}/...` (S3: `arr-hummingbird-prod-dist-git-cache`) | ~40 packages (native + forked) |

The `forked_from` field in `ci/package-overrides.yaml` determines which cache a package uses at
build time. Packages without `forked_from` default to Fedora's cache.

### How Konflux builds consume sources

Tekton PipelineRuns (generated by `ci/generate_resources.py` from Jinja2 templates) pass parameters
to the `build-rpm-package` pipeline bundle (`quay.io/hummingbird-ci/rpmbuild-pipeline`):

- `monorepo-subdir: rpms/<package>` — locates the spec, patches, and `sources` file
- `package-name: <name>` — upstream name for lookaside URL construction
- `forked-from: <url>` — (optional) selects the lookaside backend
- `dist-git-client-configdir: mock/` — (optional) points to the custom `dist-git-client.ini`

The pipeline uses `dist-git-client` to read the `sources` file and download tarballs from the
appropriate cache, then runs `mock` to build RPMs.

### Existing source generation patterns

Some native packages already have tarball generation scripts:

- `rpms/opentelemetry-collector-contrib/create-vendor-tarball.sh` — downloads upstream release,
  runs OCB to generate source code, vendors Go dependencies
- `rpms/caddy/create-vendor-tarball.sh` — downloads upstream tarball, generates Go vendor archive
- `rpms/nodejs25/packaging/make-nodejs-tarball.sh` — downloads upstream, strips bundled content

These scripts are wired into the update path via `download_sources` hooks in
`metadata/<package>.update-hooks.yaml`. The pattern is: script runs, prints output filenames to
stdout, automation uploads them to the Hummingbird lookaside cache.

## Integration with `dist_git.py update`

The current flow copies everything from Fedora dist-git (including the `sources` file) via
`shutil.copytree`. The pipeline tool inserts after this step:

```
Current:
  1. Clone Fedora dist-git
  2. copytree into rpms/<package>/        ← sources file points to Fedora lookaside
  3. Commit

Proposed:
  1. Clone Fedora dist-git
  2. copytree into rpms/<package>/        ← sources file points to Fedora lookaside
  3. Run source-pipeline tool             ← fetches from upstream, replaces sources file
  4. Upload tarballs to Hummingbird lookaside
  5. Commit                               ← sources file now points to Hummingbird lookaside
```

Implementation:

- After the `shutil.copytree` (or merge for modified packages), check if
  `metadata/<package>.source-pipeline.yaml` exists
- If it exists: invoke the pipeline tool via podman, collect outputs, upload to lookaside, replace
  the `sources` file
- If it does not exist: run the built-in default (fetch from spec URLs, upload, replace `sources`)
- A `--skip-pipeline` flag allows falling back to the current behavior during migration
- Update `ci/package-overrides.yaml` to set `forked_from` to hummingbird for each migrated package
  (so Konflux builds fetch from the Hummingbird cache)

## Integration with `check_upstream_versions.py`

The pipeline tool replaces all three hook phases from `*.update-hooks.yaml`, consolidating
per-package update behavior into a single `*.source-pipeline.yaml` file:

```
Current:
  1. update_spec hook/default             ← *.update-hooks.yaml
  2. download_sources hook/default        ← *.update-hooks.yaml
  3. post_update hook/default             ← *.update-hooks.yaml

Proposed:
  1. spec-update (pipeline YAML)          ← replaces update_spec hooks
  2. fetch + transform (pipeline YAML)    ← replaces download_sources hooks
  3. post (pipeline YAML)                 ← replaces post_update hooks
```

This reduces per-package metadata from three files (`metadata/<package>.json`,
`*.update-hooks.yaml`, `*.source-pipeline.yaml`) to two (`metadata/<package>.json` for
identity/tracking, `*.source-pipeline.yaml` for all update behavior).

Existing `*.update-hooks.yaml` files are migrated to `*.source-pipeline.yaml` definitions. During
migration, the hook system remains as a legacy fallback: if a package has a `*.update-hooks.yaml`
but no pipeline YAML, the hooks run as before. Once all 12 hook files are migrated, the hook system
is removed.

## Integration with Konflux

Initially, the pipeline tool runs pre-build (during the update automation in GitLab CI). The
tarballs it produces are uploaded to the Hummingbird lookaside, and the existing Tekton build
pipeline consumes them via `dist-git-client` as it does today.

Future: the pipeline tool could run as a Tekton task within the Konflux build pipeline itself. This
would move source generation into the SLSA-attested build environment, strengthening the provenance
chain. The container image is already compatible — it just needs a Tekton Task definition.

## Hummingbird-specific configuration

Pipeline YAML files live at `metadata/<package>.source-pipeline.yaml`.

GPG keys live at `metadata/gpg-keys/<upstream-project>.gpg`.

The container image is published as `quay.io/hummingbird-ci/source-pipeline:latest`.

## Local development

Developers can run the tool directly:

```bash
podman run --rm \
  -v ./rpms/curl:/package:ro \
  -v ./metadata/curl.source-pipeline.yaml:/pipeline.yaml:ro \
  -v ./metadata/gpg-keys:/gpg-keys:ro \
  -v /tmp/output:/output \
  quay.io/hummingbird-ci/source-pipeline:latest \
  --version 8.21.0

# Outputs in /tmp/output/:
#   curl-8.21.0.tar.xz
#   curl-8.21.0.tar.xz.asc
#   sources
#   report.json
```

## Migration Path

### Phase 1: Native Go packages

Migrate the 22 native packages that already fetch from upstream. Convert their existing
`create-vendor-tarball.sh` scripts and `download_sources` hooks into pipeline YAML definitions.
Validates the tool against known-good packages.

**Scope:** 7 packages with existing scripts + 15 without.

Of the 7 scripted packages, 4 (caddy, nats-server×2, oauth2-proxy) use an identical clone + `go mod
vendor` + tar pattern that maps directly to `vendor: {ecosystem: go}`. etcd requires
multi-submodule vendor support. The 2 otel-collector packages need a `run:` block for OCB code
generation, but their vendor step is generic.

### Phase 2: Trivial clean packages

Enable the default pipeline behavior (fetch from spec URLs) for the ~351 clean packages where
Fedora's tarball is identical to upstream. This is the highest-impact, lowest-effort phase.

**Prerequisites:**
- Audit to classify packages as trivial vs. transformed (HUM-4620)
- `dist_git.py update` integration complete (HUM-4621)

**Per-package steps:**
1. Run the pipeline tool, compare output against existing Fedora-sourced tarball
2. If identical: add `forked_from: hummingbird` to `package-overrides.yaml`
3. If different: flag for Phase 3

### Phase 3: Transformed packages

Write pipeline YAML definitions for packages where Fedora modifies the tarball. Based on the script
audit, most transforms decompose into built-in primitives:

- **Strip + repack** (nodejs×5, cyrus-sasl, perl-libnet, java in-tree libs): `strip-tarball`
- **Git snapshots** (libX11 family×5, oniguruma, php-jsqueeze): `fetch: {git: ...}`
- **Composer vendor** (composer): `vendor: {ecosystem: composer}`

Only ~7 packages (nss-fips, openssl-fips-provider, ca-certificates, gcc, gdb, python3.14, erlang27)
require `run:` blocks for genuinely custom logic.

**Scope:** determined by Phase 2 audit.

### Phase 4: Policy enforcement

Add `policy` sections to pipeline YAMLs for packages with known vendor dependency constraints.
Initially driven by CVE fixes that need to survive upstream updates.

### Phase 5: Patch verification

Enable `patches.verify` and `patches.classify` for packages. Start with warn-only
(`fail-on-unverified: false`), gather data on classification accuracy, then selectively enable
fail-on-unverified for high-risk packages.
