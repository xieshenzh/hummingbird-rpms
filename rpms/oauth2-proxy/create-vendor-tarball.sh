#!/usr/bin/bash

tag=$1

if [[ -z $tag ]]; then
    echo "This script requires the tag as an argument."
    exit 1
fi

set -euo pipefail

PKG="oauth2-proxy"
REPO="https://github.com/oauth2-proxy/$PKG"

version=${tag#v}

echo "Using tag: $tag"
echo "Using version: $version"

git -c advice.detachedHead=false clone --branch "$tag" --depth 1 "$REPO".git "$PKG-$version"
pushd "$PKG-$version"
GOPROXY='https://proxy.golang.org,direct' go mod vendor
popd
tar -C "$PKG-$version" -cjf "$PKG-$version-vendor.tar.bz2" vendor
