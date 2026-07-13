#!/usr/bin/bash
# Generate source archives for opentelemetry-collector-contrib (otelcol-contrib distribution).
#
# Source: opentelemetry-collector-releases repo (same as opentelemetry-collector).
# OCB generates _build/{main.go,components.go,go.mod,go.sum} from
# distributions/otelcol-contrib/manifest.yaml.
# go_vendor_archive then vendors from _build/ (passed directly, bare vendor/ prefix).
#
# Usage: create-vendor-tarball.sh <version>   e.g. create-vendor-tarball.sh 0.153.0

set -euo pipefail

version="${1:?version argument required}"
pkg="opentelemetry-collector-contrib"
releases_tarball="opentelemetry-collector-releases-${version}.tar.gz"
ocb_url="https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/cmd%2Fbuilder%2Fv${version}/ocb_${version}_linux_amd64"

echo "Downloading releases tarball..." >&2
curl -fSL -o "${releases_tarball}" \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/archive/v${version}/${releases_tarball}" >&2

echo "Downloading OCB v${version}..." >&2
curl -fSL -o ocb "${ocb_url}" >&2
chmod +x ocb
ocb_path="${PWD}/ocb"

workdir=$(mktemp -d)
trap 'rm -rf "${workdir}" ocb' EXIT

# Extract one level deeper so that OCB's relative replace directives resolve
# inside the workdir.  The otelcol-contrib manifest at
# distributions/otelcol-contrib/manifest.yaml uses ../../../internal/ to
# reach the repo root.  OCB copies this verbatim into _build/go.mod, so from
# _build/ the path ../../../internal/ must land inside our tree.
#
# Layout:  workdir/nest/opentelemetry-collector-releases-<ver>/
#   _build/ is at depth 3 below workdir, so ../../../ reaches workdir/ itself.
mkdir -p "${workdir}/nest"
tar -xzf "${releases_tarball}" -C "${workdir}/nest"
srcdir="${workdir}/nest/opentelemetry-collector-releases-${version}"

# otelcol-contrib 0.156.0+ includes OBI (eBPF instrumentation) which uses a
# local replace directive pointing to internal/obi-src.  The upstream
# prepare-obi.sh fetches the pre-generated source tarball into that path.
if [[ -f "${srcdir}/scripts/prepare-obi.sh" ]]; then
    echo "Preparing OBI source..." >&2
    (cd "${srcdir}" && bash scripts/prepare-obi.sh otelcol-contrib) >&2
fi

# _build/ sits at srcdir/_build/ = workdir/nest/<repo>/_build/.  The replace
# ../../../internal/ resolves to workdir/internal/.  Symlink the prepared OBI
# source there so `go mod tidy` can find it.
if [[ -d "${srcdir}/internal/obi-src" ]]; then
    echo "Symlinking OBI source for _build/go.mod resolution..." >&2
    mkdir -p "${workdir}/internal"
    ln -sfn "${srcdir}/internal/obi-src" "${workdir}/internal/obi-src"
fi

echo "Running OCB with otelcol-contrib manifest..." >&2
(cd "${srcdir}" && "${ocb_path}" --skip-compilation \
    --config distributions/otelcol-contrib/manifest.yaml) >&2

echo "Capturing OCB-generated sources from _build/..." >&2
generated_files=(go.mod go.sum)
while IFS= read -r f; do
    generated_files+=("${f}")
done < <(find "${srcdir}/_build" -maxdepth 1 -name '*.go' -printf '%P\n' | sort)
tar -C "${srcdir}/_build" \
    -cjf "${pkg}-${version}-generated.tar.bz2" \
    "${generated_files[@]}"

echo "Vendoring via go_vendor_archive (_build/ passed directly)..." >&2
go_vendor_archive create \
    -c "$(dirname "${BASH_SOURCE[0]}")/go-vendor-tools.toml" \
    -O "${pkg}-${version}-vendor.tar.bz2" \
    "${srcdir}/_build" >&2

echo "${releases_tarball}"
echo "${pkg}-${version}-generated.tar.bz2"
echo "${pkg}-${version}-vendor.tar.bz2"
