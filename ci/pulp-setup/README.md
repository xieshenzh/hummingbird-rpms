# Creating Pulp Domain and Repositories

## Overview

- This document and script should be used to initialize and setup a pulp domain and a set of
  repositories
- We choose the domain prefix of `public-` to ensure that the repositories are publicly available

## Setup

- Obtain the value of the field `cli.toml` from the secret located in the vault at
  `rhel-primitives/PULP_PUBLIC_RHEL_PRIMITIVES_CONFIG_FILE`
- Copy the contents of the field to `~/.config/pulp/cli.toml`

## Create

```bash
./create-pulp-resources.sh public-hummingbird "source,x86_64,s390x,ppc64le,aarch64"
```
