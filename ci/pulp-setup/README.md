# Creating Pulp Domain and Repositories

## Overview

- This document and script should be used to initialize and setup a pulp domain and a set of
  repositories
- We choose the domain prefix of `public-` to ensure that the repositories are publicly available

## Setup
* Obtain the value of the field `cli.toml` from the secret located in the vault at `rhel-primitives/PULP_PUBLIC_RHEL_PRIMITIVES_CONFIG_FILE`
* Save the contents to a file, e.g., `cli.toml`
* You can either copy this file to the default location `~/.config/pulp/cli.toml` or pass its path using the `--config` argument to the creation script.

## Create

Usage:

```
$ ./create-pulp-resources.sh --domain <domain_name> [OPTIONS]
```

### Options

| Option | Description |
| --- | --- |
| `--domain <name>` | **Required.** Pulp domain to create or use. |
| `--repos <list>` | Comma-separated repository names. Defaults: RPM `source,x86_64,s390x,ppc64le,aarch64`; file `metadata,rpm-catalog`. |
| `--type <type>` | Repository plugin type (e.g. `rpm`, `file`). Default: `rpm`. |
| `--config <path>` | Path to the Pulp CLI config (e.g. `cli.toml`). If omitted, the CLI default (e.g. `~/.config/pulp/cli.toml`) is used. |
| `--retain-repo-versions <n>` | **File repositories only.** Sets Pulp `retain_repo_versions` to a non-negative integer. If `--type file` and this flag is omitted, the default is **1**. The script calls `repository update` **only when** the current value differs from `<n>` (reads the live repo with `repository show --format json`). |

Run `./create-pulp-resources.sh --help` for the full usage text from the script.

### Examples

Using default config `~/.config/pulp/cli.toml`, default type `rpm`, and default RPM repositories:

```
$ ./create-pulp-resources.sh --domain public-hummingbird
```

Overriding the repositories to be created, still using default `rpm` type:

```
$ ./create-pulp-resources.sh --domain public-hummingbird --repos "source,x86_64,s390x,ppc64le,aarch64"
```

Creating `file` type repositories with default repos `metadata` and `rpm-catalog`. Retention defaults to **1** repo version per file repository (updated only if the server value is different):

```
$ ./create-pulp-resources.sh --domain public-hummingbird --type file
```

Explicit retention (file type only), for example keeping **10** repository versions:

```
$ ./create-pulp-resources.sh --domain public-hummingbird --type file --retain-repo-versions 10
```

Using a specific config file:

```
$ ./create-pulp-resources.sh --domain public-hummingbird --config ./cli.toml
```
