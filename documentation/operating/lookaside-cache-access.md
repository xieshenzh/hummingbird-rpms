---
title: Lookaside Cache Access
description: How to access the S3-based lookaside cache for RPM source tarballs
weight: 60
---

## Overview

Source tarballs for RPM packages are stored in an S3-based lookaside cache rather than in git. To
upload files to the cache (e.g., when updating a package to a new upstream version), you need AWS
credentials with the appropriate permissions.

The `ci/upload-to-lookaside-cache.sh` script handles uploads directly, and
`ci/check_upstream_versions.py --update` calls it automatically when downloading new source
archives.

## Prerequisites

You must be a **poweruser** in the `arr-cloud-aws-core` group
(`it-cloud-aws-727920394381-poweruser`). Request access to this group if you do not already have it.

## Obtaining AWS Credentials

There are two ways to authenticate.

### Option A: Browser-based login

Run `aws login`, which opens a browser for authentication:

```bash
aws login
```

### Option B: Kerberos-based login via container

Use the CKI tools container to obtain credentials via Kerberos:

```bash
$ podman run --rm -it \
    -e KRB5CCNAME=FILE:/tmp/krb5cc_$(id -u) \
    -v $HOME/.aws:/cki/.aws:U,Z \
    quay.io/cki/cki-tools:latest

bash-5.2# kinit <userid>@IPA.REDHAT.COM

bash-5.2# AWS_IDP_URL=https://auth.redhat.com/auth/realms/EmployeeIDP/protocol/saml/clients/itaws \
    cki_aws_login --duration 43200 \
    --account 727920394381 --role poweruser
```

Replace `<userid>` with your Kerberos user ID. This writes credentials to `$HOME/.aws` on the host
(bind-mounted into the container).

**Note:** This overwrites the AWS default profile credentials. If you use the default profile for
other purposes, back up `$HOME/.aws/credentials` before running this command.

## Uploading Files

Once credentials are configured in `$HOME/.aws`, you can upload files to the lookaside cache.

### Manual upload

```bash
./ci/upload-to-lookaside-cache.sh -f <file> -p <package>
```

Example:

```bash
./ci/upload-to-lookaside-cache.sh -f rpms/tar/tar-1.35.tar.xz -p tar
```

### Automated upload via version checker

`check_upstream_versions.py --update` downloads new source archives and uploads them to the
lookaside cache automatically:

```bash
./ci/check_upstream_versions.py check --update <package>
```

The `check` subcommand only processes packages with `"track_upstream": true` in their metadata when
no explicit package arguments are given. To see all packages and their upstream status, use
`check_upstream_versions.py list`. See
[Package Modification Tracking](package-modification-tracking.md) for how to enable tracking.

## See Also

- [Adding Independent Packages](adding-independent-packages.md) - Adding new packages with source tarballs
- [Rebuilding Packages](rebuilding-packages.md) - Rebuilding existing packages
