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

Example (using default config `~/.config/pulp/cli.toml`, default type `rpm`, and default repos):
```
$ ./create-pulp-resources.sh --domain public-hummingbird
```

Example (overriding the repositories to be created, still using default `rpm` type):
```
$ ./create-pulp-resources.sh --domain public-hummingbird --repos "source,x86_64,s390x,ppc64le,aarch64"
```

Example (creating `file` type repositories with default repos `metadata` and `rpm-catalog`):
```
$ ./create-pulp-resources.sh --domain public-hummingbird --type file
```

Example (using a specific config file):
```
$ ./create-pulp-resources.sh --domain public-hummingbird --config ./cli.toml
```
