---
title: GPG Source Verification
description: How upstream signing keys are stored, and how to add, rotate, or revoke them
weight: 62
aliases: [/l/gpg-source-verification]
---

## Overview

Some packages verify the authenticity of their upstream source tarball with a detached OpenPGP
signature where upstream signs each release with a private key and publishes a `.asc`/`.sig` alongside
the tarball. During source fetching, the
[gorget source-pipeline tool][source-pipeline-tool] checks that signature against the
project's **public** key before the bytes are ever used in a build.

The trusted public keys live centrally in **`metadata/gpg-keys/<project>.gpg`**, one keyring file
per upstream project. Centralized storage means:

- the full set of signers we trust is auditable in one directory, and
- rotating or revoking a key is a single, reviewable commit.

The directory is handed to gorget as `--gpg-keys-dir metadata/gpg-keys` (see
`ci/check_upstream_versions.py`), so a pipeline's `keyring:` field is just a filename within it.

## How the pieces fit together

Three things wire up verification for a package:

1. **The keyring** — `metadata/gpg-keys/<project>.gpg`, the trusted public key(s).
2. **The pipeline step** — a `verify:` entry in `metadata/<package>.source-pipeline.yaml`:

   ```yaml
   fetch:
     - type: url
       url: "https://curl.se/download/curl-${VERSION}.tar.xz"
     - type: url
       url: "https://curl.se/download/curl-${VERSION}.tar.xz.asc"

   verify:
     - type: gpg-signature
       target: "curl-${VERSION}.tar.xz"       # the artifact to verify
       signature: "curl-${VERSION}.tar.xz.asc" # its detached signature
       keyring: "curl.gpg"                      # filename in metadata/gpg-keys/
   ```

   gorget imports `keyring` into a fresh, throwaway GPG homedir per check, then runs the equivalent
   of `gpg --verify <signature> <target>`. A bad or missing signature fails the fetch.

3. **CI validation** — `test/test_gpg_keys.py` (run by `make check`) confirms every file in
   `metadata/gpg-keys/` is a parseable public key, and that every `keyring:` referenced by a
   pipeline actually exists.

## Adding a key for a new package

Prerequisite: `gpg` (from the `gnupg2` package). It ships in the CI image, run the
commands below inside a container, e.g.
`podman run --rm -it -v "$PWD:$PWD:z" -w "$PWD" quay.io/hummingbird-ci/gitlab-ci:latest bash`.

1. **Obtain the upstream public key.** Prefer a key you can already trust: many packages already
   ship the maintainer's key next to their spec (e.g. `rpms/curl/mykey.asc`,
   `rpms/bash/chet-gpgkey.asc`). Otherwise download it from the project's official key page.

2. **Store it as a keyring in `metadata/gpg-keys/`.** The keyring may be ASCII-armored or binary;
   this repo standardizes on binary `.gpg`. Convert an armored key with `--dearmor`:

   ```bash
   gpg --dearmor < rpms/<package>/<upstream-key>.asc > metadata/gpg-keys/<project>.gpg
   ```

   Name the file after the upstream **project**, not the RPM (so multiple versioned packages, e.g.
   `python3.11`/`python3.12`, can share one keyring).

3. **Verify the key is what you expect.** Print its fingerprints and confirm they match the
   fingerprints published on the upstream's official channel:

   ```bash
   gpg --show-keys --with-fingerprint metadata/gpg-keys/<project>.gpg
   ```

4. **Wire up the pipeline.** Add (or extend) `metadata/<package>.source-pipeline.yaml` with the
   `fetch` steps for the tarball + signature and the `verify: [{type: gpg-signature, ...}]` step
   shown above.

5. **Prove the whole chain end to end** before committing import the keyring into a throwaway
   homedir and verify a real release signature against it:

   ```bash
   V=<version>
   curl -fsSLO "https://<upstream>/<tarball>-$V.tar.xz"
   curl -fsSLO "https://<upstream>/<tarball>-$V.tar.xz.asc"
   export GNUPGHOME=$(mktemp -d)
   gpg --import metadata/gpg-keys/<project>.gpg
   gpg --verify "<tarball>-$V.tar.xz.asc" "<tarball>-$V.tar.xz"   # expect "Good signature"
   ```

6. **Run the checks:** `make check` (validates the keyring and the pipeline reference).

## Rotating or replacing a key

Upstream may roll to a new signing key (expiry, policy, new maintainer). Because the trusted set is
just files in one directory, rotation is a single commit:

1. Obtain the new public key from the upstream's official channel and confirm its fingerprint out
   of band.
2. Replace the contents of `metadata/gpg-keys/<project>.gpg` (re-run the `--dearmor` step). If
   upstream signs a transition period with both keys, you may keep both by importing them into the
   same keyring:

   ```bash
   gpg --dearmor < old-key.asc  > metadata/gpg-keys/<project>.gpg
   gpg --dearmor < new-key.asc >> metadata/gpg-keys/<project>.gpg
   ```

3. Re-run the end-to-end verification (step 5 above) against the latest release, then `make check`.
4. Commit with a message recording *why* the key changed and how you confirmed the new fingerprint,
   this file is the audit trail for what we trust.

## Revoking / removing a key

- If a project drops GPG verification, delete both `metadata/gpg-keys/<project>.gpg` and the
  `verify:` step from its pipeline in the same commit. (CI fails a pipeline that references a
  missing keyring, and if the key is orphaned the reverse is easy to spot.)
- If a key is compromised, remove it immediately and replace it with the upstream's revocation /
  replacement key.

## Troubleshooting

- **`gpg: no valid OpenPGP data found`** — the file isn't a real keyring (empty, truncated, or you
  saved an HTML error page). Re-download and re-`--dearmor`. `test_gpg_keys.py` catches this in CI.
- **`gpg: Can't check signature: No public key`** — the signature was made with a key that isn't in
  the keyring. Upstream likely rotated keys, follow *Rotating or replacing a key* above.
- **`BAD signature`** — the tarball does not match the signature. Do **not** paper over this. It
  means a corrupted download or, in the worst case, tampering. Re-fetch from the canonical source and
  if it persists, escalate rather than accepting the artifact.

[source-pipeline-tool]: https://hummingbird-project.io/l/source-pipeline-tool
