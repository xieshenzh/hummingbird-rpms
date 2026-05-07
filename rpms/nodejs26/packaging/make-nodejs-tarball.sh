#!/bin/bash
# deps: coreutils curl tar
set -e

# Print usage
usage() {
    printf '%s <VERSION>\n' "$(basename "$0")"
    printf '%s\n' "Create a NodeJS tarball for given VERSION stripped of parts we cannot ship."
}

# Log a message
log() {
    printf '## %s\n' "$*"
}

# Tweak default cURL flags
curl() {
    command curl --progress-bar "$@"
}

readonly VERSION="$1"; if test -z "$VERSION"; then
    printf '%s\n\n' "No VERSION specified!"
    usage >&2
    exit 1
fi

readonly BASENAME="node-v${VERSION}"
readonly BASEURL="https://nodejs.org/dist/v${VERSION}"

log >&2 Downloading and verifying "${BASENAME}.tar.gz"
curl --remote-name "${BASEURL}/${BASENAME}.tar.gz"
curl --silent "${BASEURL}/SHASUMS256.txt" | sha256sum --check --ignore-missing

log >&2 Repackaging the sources into "${BASENAME}-stripped.tar.gz"
tar -xzf "${BASENAME}.tar.gz" && rm -f "${BASENAME}.tar.gz"
rm -rf "${BASENAME}/deps/openssl"
tar -czf "${BASENAME}-stripped.tar.gz" "${BASENAME}"
