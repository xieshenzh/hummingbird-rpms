---
title: Private RPM Repositories
weight: 55
aliases: [/l/private-rpm-repositories]
---

> **AI Agent Note:** When asked to route a package to a private repository, add
> `private_product: <product>` to the package's entry in `ci/package-overrides.yaml`,
> then run `make generate` to regenerate resources. The product name must match an
> existing RPA entry in `ci/konflux_rpa_config.yml`.

## Overview

Hummingbird LTS products (e.g., LTS OpenJDK, LTS DotNet) require subscription-gated RPMs that
must not be publicly accessible. Each LTS product gets its own private Pulp repository with
independent credentials, enabling per-product subscription enforcement.

The private RPM pipeline uses the same build infrastructure as public RPMs but routes packages
to separate Pulp domains via dedicated Konflux Applications and ReleasePlanAdmissions (RPAs).

### How it works

1. Each private product has a dedicated **Konflux Application** (e.g.,
   `private-<product>-rpms-main`) separate from the public `rpms-main` application.
2. Packages are assigned to a private product via `private_product` in
   `ci/package-overrides.yaml`.
3. A dedicated **RPA** per product routes those packages to private Pulp repositories.
4. The public RPA uses `exclude_private: true` to exclude private packages from public repos.

### Architecture

```text
package-overrides.yaml          konflux_rpa_config.yml
  <package>:                      rpas:
    private_product: <product>      - name: ...public...
                                      exclude_private: true
                                    - name: ...private-<product>...
                                      private_product: <product>
        |                                    |
        v                                    v
  Component resource              ReleasePlanAdmission
  application: private-           targets: private-<product>-rpms-main
    <product>-rpms-main           pulp_signed_domain: private-hummingbird-<product>
```

## Assigning a Package to a Private Product

Add `private_product` to the package's entry in `ci/package-overrides.yaml`:

```yaml
<package>:
  private_product: <product>
  timeout_hours: 8
```

Then regenerate all resources:

```bash
make generate
```

This will:

- Set the package's Konflux Component to use `application: private-<product>-rpms-main`
- Include the package in the private product's RPA component list
- Exclude the package from the public RPA (when `exclude_private: true` is set)

### Verifying the assignment

After running `make generate`, verify the changes:

```bash
# Check the component's application assignment
grep -A5 '<package>-main' konflux-templates/rendered.yml | grep application

# Check the package appears in the private RPA
grep '<package>-main' releng/hummingbird-rpms-private-<product>.yaml

# Check the package is excluded from the public RPA
grep '<package>-main' releng/hummingbird-rpms-tech-preview-staging.yaml
# (should return no results)
```

## Onboarding a New Private Product

Adding a new LTS product (beyond existing ones) requires these steps: service account,
Pulp infrastructure, credentials secret, Konflux Application, RPA configuration,
resource regeneration, and content guard setup.

### 1. Create a Pulp service account (optional)

If you want isolated credentials for private repo operations (recommended), create a
dedicated service account before setting up the Pulp infrastructure. See
[Pulp Access](pulp-access.md) for instructions on creating a service account, configuring
the CLI, and storing credentials in the vault. Otherwise, you can reuse the existing public
Pulp credentials.

### 2. Create Pulp infrastructure

Use the existing Pulp setup script to create the private domains, RPM repositories, and
file repositories (for SBOMs and attestations):

```bash
# Unsigned (staging) - RPM repos
./ci/pulp-setup/create-pulp-resources.sh \
  --domain private-hummingbird-<product>-unsigned

# Unsigned (staging) - file repos (SBOM/attestations)
./ci/pulp-setup/create-pulp-resources.sh \
  --domain private-hummingbird-<product>-unsigned \
  --type file

# Signed (production) - RPM repos
./ci/pulp-setup/create-pulp-resources.sh \
  --domain private-hummingbird-<product>

# Signed (production) - file repos (SBOM/attestations)
./ci/pulp-setup/create-pulp-resources.sh \
  --domain private-hummingbird-<product> \
  --type file
```

If using a dedicated service account, add `--config <path-to-cli.toml>` to each command.

This creates per-architecture RPM repositories (`source`, `x86_64`, `s390x`, `ppc64le`,
`aarch64`) with distributions, and file repositories (`metadata`, `rpm-catalog`) for
SBOM and attestation storage.

### 3. Deploy the Pulp credentials secret

If using a dedicated service account (step 1), add a Kubernetes Secret template to the
**infrastructure repo** so the credentials are deployed to the Konflux cluster:

1. Create `kubernetes/setup-konflux/47-pulp-private-hummingbird-config-file-secret.yml.j2`:

   ```yaml
   ---
   kind: Secret
   apiVersion: v1
   metadata:
     name: pulp-private-hummingbird-config-file-secret
   stringData:
     cli.toml: «{ lookup_secret("HUMMINGBIRD_PRIVATE_PULP_BOT_CONFIG_FILE[deployed]:cli.toml") | forceescape }»
   type: Opaque
   ```

2. Add an entry to `secrets.yml` for the vault key:

   ```yaml
   HUMMINGBIRD_PRIVATE_PULP_BOT_CONFIG_FILE:
     backend: hv
     meta:
       active: true
       created_at: '<timestamp>'
       deployed: true
   ```

   The secret name (`pulp-private-hummingbird-config-file-secret`) must match the
   `pulp_secret_name` in the private RPA configuration (step 5).

3. Add an ExternalSecret to the **konflux-release-data repo** (`rhtap-release-data`) so the
   credentials are available to the release pipeline. Create the ExternalSecret in the
   cluster-specific directory (e.g., `tenants-config/cluster/kflux-prd-rh03/managed/rhtap-releng-tenant/`):

   ```yaml
   ---
   apiVersion: external-secrets.io/v1
   kind: ExternalSecret
   metadata:
     name: hummingbird-private-pulp-bot-production-secret
     annotations:
       argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true
       argocd.argoproj.io/sync-wave: "0"
   spec:
     dataFrom:
       - extract:
           key: releng/konflux/rhtap-releng-tenant/public-network/hummingbird-private-pulp-bot-production
     refreshInterval: 1h
     secretStoreRef:
       kind: SecretStore
       name: releng-vault
     target:
       creationPolicy: Owner
       deletionPolicy: Delete
       name: hummingbird-pulp-credentials-private-production-secret
   ```

   Add it to the cluster's `kustomization.yaml` and regenerate auto-generated files with
   `tenants-config/build-manifests.sh`.

4. Add the secret to the **releng vault** at `https://vault.devshift.net` under the path
   `releng/konflux/rhtap-releng-tenant/public-network/hummingbird-private-pulp-bot-production`
   with the following keys:

   | Key | Value |
   | --- | --- |
   | `cli.toml` | Full contents of the Pulp CLI config file |
   | `expires-on` | `na` |
   | `owner` | Your Kerberos username |

### 4. Create the Konflux Application

The Application resource is managed in the **infrastructure repo**, not this repo. Create it
there following the same pattern as `rpms-main`:

1. Create a new directory `kubernetes/private-<product>-rpms-main/` in the infrastructure repo.

2. Add `00-application.yml.j2` with the standard Application template:

   ```yaml
   ---
   apiVersion: appstudio.redhat.com/v1alpha1
   kind: Application
   metadata:
     name: {{ env["PROJECT_NAME"] }}
   spec:
     appModelRepository: {url: ""}
     displayName: {{ env["PROJECT_NAME"] }}
     gitOpsRepository: {url: ""}
   ```

3. Add the new project to the CI matrix in `infrastructure/.gitlab-ci.yml`. Find the
   `PROJECT_NAME` list under the `konflux-rh03/hummingbird-tenant` context and add the
   new application name:

   ```yaml
   - PROJECT_NAME:
       - rpms-main
       - private-<product>-rpms-main   # add this line
     PROJECT_CONTEXT:
       - konflux-rh03/hummingbird-tenant
   ```

4. Add `01-release-plans.yml.j2` with a ReleasePlan that references the private RPA.
   Copy from `kubernetes/rpms-main/01-release-plans.yml.j2` and update the
   `releasePlanAdmission` label to match the private RPA name:

   ```yaml
   metadata:
     name: hummingbird-rpm-release-private-<product>
     labels:
       release.appstudio.openshift.io/auto-release: "true"
       release.appstudio.openshift.io/standing-attribution: "true"
       release.appstudio.openshift.io/releasePlanAdmission: hummingbird-rpms-private-<product>
   spec:
     application: {{ env["PROJECT_NAME"] }}
     target: rhtap-releng-tenant
     # ... copy remaining spec from rpms-main/01-release-plans.yml.j2
   ```

   The ReleasePlan connects the private Application to its ReleasePlanAdmission. Without
   it, builds in the private Application will not trigger releases.

5. Optionally copy IntegrationTestScenario templates from `kubernetes/rpms-main/` if the
   private application needs them.

Merge this MR in the infrastructure repo first — the CI pipeline will deploy the Application
and ReleasePlan to the cluster. Components and RPAs in this repo reference it by name, so
the Application must exist before they are applied.

### 5. Add RPA configuration

Add a new entry to the `rpas:` list in `ci/konflux_rpa_config.yml`:

```yaml
rpas:
  # Existing public RPA (ensure exclude_private: true is set)
  - name: hummingbird-rpms-tech-preview-staging
    exclude_private: true
    # ... existing config ...

  # New private product RPA
  - name: hummingbird-rpms-private-<product>
    application_prefix: private-<product>-rpms
    release_org: registry.stage.redhat.io/hummingbird-tech-preview
    single_component_mode: true
    service_account_name: hummingbird-rpm-release-staging
    pulp_unsigned_domain: private-hummingbird-<product>-unsigned
    pulp_signed_domain: private-hummingbird-<product>
    pulp_secret_name: hummingbird-pulp-credentials-private-production-secret
    pipeline_revision: <release-pipeline-branch>
    pipeline_url: https://github.com/scoheb/release-service-catalog.git
    private_product: <product>
    component_filter:
      path_prefix: rpms/
```

Key fields:

| Field | Purpose |
| --- | --- |
| `name` | Kubernetes resource name for the RPA |
| `application_prefix` | Combined with branch to form the Konflux Application name |
| `private_product` | Matches the `private_product` value in `package-overrides.yaml` |
| `pulp_unsigned_domain` | Pulp domain for unsigned RPMs (staging) |
| `pulp_signed_domain` | Pulp domain for signed RPMs (production) |
| `pulp_secret_name` | Kubernetes secret containing Pulp publishing credentials |

### 6. Assign packages and regenerate

Add `private_product: <product>` to each package in `ci/package-overrides.yaml`, then
regenerate:

```bash
make generate
```

This produces:

- Updated `konflux-templates/rendered.yml` with per-component application assignments
- A new RPA file at `releng/hummingbird-rpms-private-<product>.yaml`
- Updated public RPA excluding private packages

### 7. Set up Pulp content guard

To restrict access to the private Pulp repositories, a content guard must be configured
so only customers with the correct subscription can access the content:

1. Obtain a **SKU** for the private product's subscription offering.
2. Create a **feature** for that SKU in the Feature service.
3. Contact the **Pulp team** to:
   - Map the organization ID to the feature
   - Set up the content guard on the private Pulp domain(s)

This step is required before customers can access the private repositories. Without it,
the repositories are created but have no access control.

### Pulp credentials

If using a dedicated service account (step 1), see [Pulp Access](pulp-access.md) for
vault storage instructions.

If using the shared service account, private RPAs reuse the same Pulp publishing credentials
as the public RPA (`hummingbird-pulp-credentials-production-secret`). Per-product access
control for customers is handled downstream by Red Hat's subscription entitlement system,
not at the Pulp publishing layer.

## Controlling Public/Private Routing

The `exclude_private` flag on the public RPA controls whether private packages are excluded
from public repositories:

| `exclude_private` | Behavior |
| --- | --- |
| `true` (default) | Private packages appear only in their product's private RPA |
| `false` | Private packages appear in both the public and private RPAs |

To publish a package to both public and private repos, set `exclude_private: false` on the
public RPA entry in `ci/konflux_rpa_config.yml`.

## Related Files

| File | Purpose |
| --- | --- |
| `ci/package-overrides.yaml` | Per-package `private_product` assignment |
| `ci/konflux_rpa_config.yml` | RPA definitions (public + private) |
| `ci/generate_resources.py` | Generates Components, RPAs, and PipelineRuns |
| `ci/pulp-setup/create-pulp-resources.sh` | Creates Pulp domains and repositories |
| `konflux-templates/macros/releng/release-plan-admission.yml.j2` | RPA template |
| `konflux-templates/macros/component.yml.j2` | Component template (per-component application) |
| `konflux-templates/macros/image-repository.yml.j2` | ImageRepository template (per-component application) |
