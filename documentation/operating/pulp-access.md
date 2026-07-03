---
title: Pulp Access
weight: 45
aliases: [/l/pulp-access]
---

## Overview

Pulp is used to host and distribute Hummingbird RPM repositories. Access to the Pulp API
requires a service account with credentials stored in the vault.

## Existing credentials

The public Pulp service account credentials are stored in the vault at
`rhel-primitives/PULP_PUBLIC_RHEL_PRIMITIVES_CONFIG_FILE`. Retrieve the `cli.toml` field
and save it locally (see [ci/pulp-setup/README.md](../../ci/pulp-setup/README.md) for
usage).

## Creating a new Pulp service account

When you need a dedicated service account (e.g., for private product repositories):

1. Go to <https://access.redhat.com/terms-based-registry/>
2. Log in with your `@redhat.com` account
3. Click **New Service Account**
4. Enter a username like `hummingbird-<qualifier>-pulp-bot`
5. Enter a description like `bot account for hummingbird <qualifier> pulp operations`
6. Click **Create**
7. Capture the full username (in the format `<id>|<username>`) and the token

### Configure the CLI

Create a `cli.toml` with the new credentials:

```toml
[cli]
base_url = "https://packages.redhat.com"
api_root = "/api/pulp/"
username = "<id>|hummingbird-<qualifier>-pulp-bot"
password = "<token>"
verify_ssl = true
format = "json"
dry_run = false
timeout = 0
verbose = 0
domain = "<target-domain>"
```

You can either save this to `~/.config/pulp/cli.toml` or pass it explicitly with
`--config ./cli.toml` when running scripts.

### Store credentials in the vault

Store the credentials in the vault at `apps/hummingbird` with the key
`HUMMINGBIRD_<QUALIFIER>_PULP_BOT_CONFIG_FILE` (e.g.,
`HUMMINGBIRD_PRIVATE_PULP_BOT_CONFIG_FILE`). Create two subkeys:

- `cli.toml` — the full config file contents
- `value` — the token value

## Related

- [Lookaside Cache Access](lookaside-cache-access.md) — AWS credentials for source tarball uploads
- [Private RPM Repositories](private-rpm-repositories.md) — setting up private per-product Pulp repos
- [ci/pulp-setup/README.md](../../ci/pulp-setup/README.md) — Pulp domain and repository creation script
