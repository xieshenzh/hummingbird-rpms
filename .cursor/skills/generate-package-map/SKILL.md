---
name: generate-package-map
description: >-
  Populate upstream_repo (and related fields) in per-package metadata JSON
  files. Use when the user asks to update the package map, add new packages,
  refresh upstream mappings, or says /generate-package-map.
---

# Generate Package Map

Populate `upstream_repo` and related fields in the per-package
`metadata/<package>.json` files, mapping Hummingbird source RPM package
names to their canonical upstream git repositories.

## Fields

Each `metadata/<package>.json` file should contain:

| Field               | Required | Description                                                              |
| ------------------- | -------- | ------------------------------------------------------------------------ |
| `upstream_repo`     | Yes      | Canonical upstream git repository URL                                    |
| `upstream_branch`   | No       | Upstream branch (only for versioned packages sharing a repo)             |
| `cve_product`       | No       | CVE vendor/product override (`Vendor / Product`, or a list for multiple) |
| `version_transform` | No       | Version transform rule (e.g. `dotnet_sdk_to_runtime`)                    |

When no upstream repo exists, use the Fedora DistGit URL
`https://src.fedoraproject.org/rpms/<name>` as the fallback.

## Procedure

### Step 1: Find packages missing upstream_repo

```bash
cd "$(git rev-parse --show-toplevel)"
python3 -c "
import json
from pathlib import Path
for f in sorted(Path('metadata').glob('*.json')):
    data = json.load(open(f))
    if 'upstream_repo' not in data:
        print(f.stem)
"
```

### Step 2: Find upstream repos

For each package missing `upstream_repo`, find the canonical upstream git
repository.

Use WebSearch in batches of 5-6 at a time:

```text
<package-name> upstream source git repository
```

#### Mirror canonicalization

Always replace mirrors with the canonical upstream:

| If you find this                          | Use the canonical URL instead                      |
| ----------------------------------------- | -------------------------------------------------- |
| `github.com/bminor/<name>`                | `https://sourceware.org/git/<name>.git`            |
| `github.com/sourceware-mir/<name>`        | `https://sourceware.org/git/<name>.git`            |
| `github.com/gitGNU/gnu_<name>`            | `https://git.savannah.gnu.org/git/<name>.git`      |
| `github.com/gnu-mirror-unofficial/<name>` | `https://git.savannah.gnu.org/git/<name>.git`      |
| `github.com/autotools-mirror/<name>`      | `https://git.savannah.gnu.org/git/<name>.git`      |
| `github.com/gmp-mirror/<name>`            | `https://git.savannah.nongnu.org/git/<name>.git`   |
| `github.com/GNOME/<name>`                 | `https://gitlab.gnome.org/GNOME/<name>`            |
| `github.com/gcc-mirror/gcc`               | `https://gcc.gnu.org/git/gcc.git`                  |
| `github.com/mirror/busybox`               | `https://git.busybox.net/busybox`                  |
| `github.com/mirror/sed`                   | `https://git.savannah.gnu.org/git/sed.git`         |
| `github.com/mirror/ncurses`               | Fedora DistGit (no canonical git repo)             |
| `github.com/coreutils/coreutils`          | `https://git.savannah.gnu.org/git/coreutils.git`   |
| `github.com/freetype/freetype`            | `https://gitlab.freedesktop.org/freetype/freetype` |
| `github.com/libssh/libssh-mirror`         | `https://git.libssh.org/projects/libssh.git`       |
| `github.com/gpg/<name>`                   | `https://dev.gnupg.org/source/<name>.git`          |
| `github.com/gnutls/<name>`                | `https://gitlab.com/gnutls/<name>`                 |
| `github.com/haproxy/haproxy`              | `https://git.haproxy.org/git/haproxy.git`          |
| `github.com/openldap/openldap`            | `https://git.openldap.org/openldap/openldap`       |

#### Known overrides

These packages are commonly misidentified by web search. Use the listed URL
directly instead of whatever web search returns:

| Package             | Correct URL                                                    | Notes                              |
| ------------------- | -------------------------------------------------------------- | ---------------------------------- |
| `bind`              | `https://gitlab.isc.org/isc-projects/bind9`                    | GitHub is a mirror                 |
| `dav1d`             | `https://code.videolan.org/videolan/dav1d`                     | GitHub is a mirror                 |
| `freetype`          | `https://gitlab.freedesktop.org/freetype/freetype`             | GitHub is a mirror                 |
| `man-db`            | `https://gitlab.com/man-db/man-db`                             | GitHub is a mirror                 |
| `nettle`            | `https://git.lysator.liu.se/nettle/nettle`                     | GitHub is a mirror                 |
| `python-rpm-macros` | `https://src.fedoraproject.org/rpms/python-rpm-macros`         | Fedora infra package               |
| `redhat-rpm-config` | `https://src.fedoraproject.org/rpms/redhat-rpm-config`         | Fedora infra package               |
| `selinux-policy`    | `https://github.com/fedora-selinux/selinux-policy`             | Not the userspace tools repo       |
| `tar`               | `https://git.savannah.gnu.org/git/tar.git`                     | Not the maintainer's personal fork |
| `aom`               | `https://aomedia.googlesource.com/aom`                         | GitHub is a mirror                 |
| `haproxy`           | `https://git.haproxy.org/git/haproxy.git`                      | GitHub is a mirror                 |
| `iproute`           | `https://git.kernel.org/pub/scm/network/iproute2/iproute2.git` | GitHub is a mirror                 |
| `libcap`            | `https://git.kernel.org/pub/scm/libs/libcap/libcap.git`        | GitHub is a mirror                 |
| `libidn2`           | `https://gitlab.com/libidn/libidn2`                            | GitHub is a mirror                 |
| `libtirpc`          | `https://src.fedoraproject.org/rpms/libtirpc`                  | No HTTPS git endpoint available    |
| `openldap`          | `https://git.openldap.org/openldap/openldap`                   | GitHub is a mirror                 |
| `svt-av1`           | `https://gitlab.com/AOMediaCodec/SVT-AV1`                      | Not on GitHub                      |

#### Shared-repo packages

Some packages share an upstream repo with another package and have no
separate upstream of their own. Use the same repo (and branch, if any):

| Package                    | Same repo as      |
| -------------------------- | ----------------- |
| `golang-fipsX.YY`          | `golangX.YY`      |
| `nss-fips`                 | `nss`             |
| `openssl-fips-provider`    | `openssl`         |
| `systemd-stub`             | `systemd`         |
| `java-XX-openjdk-portable` | `java-XX-openjdk` |

#### URL validation

After finding a URL, reject and re-search if it has any of these problems:

- Contains `/cgit/` in the path (web interface URL; remove the `/cgit` segment)
- Contains `?p=` query string (gitweb URL; convert to a proper git URL)
- Contains `/plain/` or `/raw/` path segments (raw file URL, not a repo)
- Points to a personal GitHub fork (`github.com/<individual-username>/...`)

#### Fedora DistGit fallback

For packages with no upstream git repo (Fedora-only infra packages, RPM
macro packages, etc.), use `https://src.fedoraproject.org/rpms/<name>`.

### Step 3: Set branches for versioned packages

Packages that share a single upstream repo across versions need
`upstream_branch`:

| Package pattern   | Repo                                       | Branch pattern          |
| ----------------- | ------------------------------------------ | ----------------------- |
| `python3.XX`      | `https://github.com/python/cpython`        | `3.XX`                  |
| `nodejsXX`        | `https://github.com/nodejs/node`           | `vXX.x`                 |
| `rubyX.Y`         | `https://github.com/ruby/ruby`             | `ruby_X_Y`              |
| `golangX.YY`      | `https://github.com/golang/go`             | `release-branch.goX.YY` |
| `golang-fipsX.YY` | `https://github.com/golang/go`             | `release-branch.goX.YY` |
| `dotnetX.Y`       | `https://github.com/dotnet/runtime`        | `release/X.Y`           |
| `mariadbX.YY`     | `https://github.com/MariaDB/server`        | `X.YY`                  |
| `tomcatXX`        | `https://github.com/apache/tomcat`         | `XX.Y.x` or `main`      |
| `postgresqlXX`    | `https://github.com/postgres/postgres`     | `REL_XX_STABLE`         |
| `kubernetesX.YY`  | `https://github.com/kubernetes/kubernetes` | `release-X.YY`          |
| `java-XX-openjdk` | `https://github.com/openjdk/jdkXXu`        | (default)               |

### Step 4: Update the metadata JSON files

For each package, update `metadata/<package>.json`:

1. Load the existing JSON
2. Set `upstream_repo` to the upstream URL
3. If applicable, set `upstream_branch`, `cve_product`, `version_transform`
4. Write back with `json.dump(data, f, indent=2, sort_keys=True)` and a
   trailing newline

#### Known CVE product overrides

These packages have `cve_product` set to resolve multi-product CVEs.
The value can be `Vendor / Product` for an exact match, just `Vendor`
for a vendor-only partial match, or a list of selectors when a package
maps to more than one CVE product (match-any):

| Package   | `cve_product`                                         | Reason                                                              |
| --------- | ----------------------------------------------------- | ------------------------------------------------------------------- |
| `nginx`   | `F5 / NGINX Open Source`                              | CVE also lists NGINX Plus with incompatible R-versioning            |
| `golang*` | `` `Go ` `` (note trailing space)                     | Vendor prefix match; trailing space prevents matching "Google" etc. |
| `busybox` | `["vda-linux / busybox_mirror", "BusyBox / BusyBox"]` | Two upstream identifiers for the same package                       |

## Adding a single new package

1. Web-search for the upstream git repository
2. Apply the canonicalization and validation rules above
3. Add `upstream_repo` to `metadata/<package>.json`
4. If the package is versioned, set `upstream_branch` per the table
5. If no upstream git repo exists, use `https://src.fedoraproject.org/rpms/<name>`

## File location

- Metadata files: `metadata/<package>.json`
