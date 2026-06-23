#!/usr/bin/bash
#
# Create the openssl-fips-provider source tarball.
#
# This script downloads the Red Hat openssl-fips-provider source RPM from the
# public UBI source repository, extracts the embedded gold RPM bundle, then
# repackages those RPMs into the source tarball used by this package.
#
# Usage: ./create-source-tarball.sh
#
# Requirements:
# - podman
# - rpm2cpio
# - cpio
# - tar
#
set -euo pipefail

VERSION="3.0.7"
RHEL_RELEASE="11.el9_8"
GOLD_RELEASE="11.el9_0"
OVR="${VERSION}-${RHEL_RELEASE}"
GOLD_OVR="${VERSION}-${GOLD_RELEASE}"
PKGNAME="openssl-fips-provider"
SOURCE_RPM="${PKGNAME}-${OVR}.src.rpm"
RHEL_TARBALL="${PKGNAME}-${VERSION}-1.tar.gz"
TARBALL_NAME="${PKGNAME}-${VERSION}"

# Architectures Hummingbird builds today.
ARCHES=(x86_64 aarch64)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/${TARBALL_NAME}"
DOWNLOAD_DIR="${SCRIPT_DIR}/download-${TARBALL_NAME}"

echo "=== Creating openssl-fips-provider source tarball ==="
echo "Version: ${VERSION}"
echo "RHEL Package Release: ${RHEL_RELEASE}"
echo "Gold Artifact Release: ${GOLD_RELEASE}"
echo "Architectures: ${ARCHES[*]}"
echo ""

rm -rf "${WORK_DIR}" "${DOWNLOAD_DIR}"
mkdir -p "${WORK_DIR}" "${DOWNLOAD_DIR}"

echo "=== Downloading RHEL source RPM from UBI source repository ==="
podman run --rm \
    -v "${DOWNLOAD_DIR}:/output:Z" \
    registry.access.redhat.com/ubi9/ubi:latest \
    /bin/bash -lc "cd /output && dnf download --source ${PKGNAME}-${OVR}"

if [[ ! -f "${DOWNLOAD_DIR}/${SOURCE_RPM}" ]]; then
    echo "ERROR: missing ${SOURCE_RPM}"
    exit 1
fi

echo "=== Extracting embedded gold artifact bundle ==="
rpm2cpio "${DOWNLOAD_DIR}/${SOURCE_RPM}" | (cd "${WORK_DIR}" && cpio -id --quiet)

if [[ ! -f "${WORK_DIR}/${RHEL_TARBALL}" ]]; then
    echo "ERROR: missing ${RHEL_TARBALL} in ${SOURCE_RPM}"
    exit 1
fi

tar -xf "${WORK_DIR}/${RHEL_TARBALL}" -C "${WORK_DIR}"
rm -f "${WORK_DIR}/${RHEL_TARBALL}"
rm -f "${WORK_DIR}/${PKGNAME}.spec" "${WORK_DIR}/extract-src.sh" "${WORK_DIR}/extract-fips.sh" "${WORK_DIR}/README.md"

echo "=== Verifying embedded RPMs ==="
REQUIRED_FILES=("${PKGNAME}-${GOLD_OVR}.src.rpm")
for arch in "${ARCHES[@]}"; do
    REQUIRED_FILES+=(
        "${PKGNAME}-so-${GOLD_OVR}.${arch}.rpm"
        "${PKGNAME}-so-debuginfo-${GOLD_OVR}.${arch}.rpm"
        "${PKGNAME}-debugsource-${GOLD_OVR}.${arch}.rpm"
    )
done

missing=0
for file in "${REQUIRED_FILES[@]}"; do
    if [[ -f "${WORK_DIR}/${file}" ]]; then
        echo "OK: ${file}"
    else
        echo "MISSING: ${file}"
        missing=1
    fi
done

if [[ "${missing}" -eq 1 ]]; then
    echo "ERROR: required files are missing"
    exit 1
fi

echo "=== Creating tarball ==="
(
    cd "${WORK_DIR}"
    tar -czvf "${SCRIPT_DIR}/${TARBALL_NAME}.tar.gz" *.rpm
)

echo "=== Generating SHA512 checksum ==="
CHECKSUM=$(sha512sum "${SCRIPT_DIR}/${TARBALL_NAME}.tar.gz" | awk '{print $1}')
echo "SHA512 (${TARBALL_NAME}.tar.gz) = ${CHECKSUM}" > "${SCRIPT_DIR}/sources"

echo "=== Updated sources file ==="
cat "${SCRIPT_DIR}/sources"

rm -rf "${WORK_DIR}" "${DOWNLOAD_DIR}"

echo "=== Done ==="
echo "Tarball: ${SCRIPT_DIR}/${TARBALL_NAME}.tar.gz"
echo "Sources file updated with checksum"
