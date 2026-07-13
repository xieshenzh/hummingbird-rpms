#!/usr/bin/bash

set -euo pipefail

version="${1:?version argument required}"
archive="prometheus-${version}.tar.gz"
output="prometheus-${version}-vendor.tar.bz2"
workdir=$(mktemp -d)
trap 'chmod -R u+w "${workdir}"; rm -rf "${workdir}"' EXIT

tar -xzf "${archive}" -C "${workdir}"
source_dir="${workdir}/prometheus-${version}"

(
    cd "${source_dir}"
    GOWORK=off GOCACHE="${workdir}/gocache" GOMODCACHE="${workdir}/gomodcache" \
        GOPROXY='https://proxy.golang.org,direct' go mod vendor
)

tar -C "${source_dir}" -cjf "${output}" vendor
