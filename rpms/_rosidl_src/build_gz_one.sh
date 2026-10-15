#!/usr/bin/env bash
# build_gz_one.sh NAME VER VENDOR_REPO SRC_URL
# Downloads Source0 (gz_*_vendor tag) + Source1 (upstream lib tarball) into the
# package dir, builds via mock, copies the non-debug RPM into ~/ros-local-rpms.
set -uo pipefail
NAME="$1"; VER="$2"; VREPO="$3"; SRCURL="$4"
HB="$HOME/hummingbird-rpms"; dir="$HB/rpms/$NAME"
cd "$dir" || exit 1
[ -f "$NAME-$VER.tar.gz" ] || curl -fsSL "https://github.com/$VREPO/archive/refs/tags/$VER.tar.gz" -o "$NAME-$VER.tar.gz" || { echo "SRC0 DL FAIL"; exit 1; }
s1name="${SRCURL##*#/}"; s1url="${SRCURL%%#*}"
[ -f "$s1name" ] || curl -fsSL "$s1url" -o "$s1name" || { echo "SRC1 DL FAIL"; exit 1; }
sudo rm -rf /tmp/rpm-build/{sources,results,config,RPMS,SRPMS}/* 2>/dev/null || true
cd "$HB"
./ci/build_rpms.sh --build-dir /tmp/rpm-build --local-rpms-dir "$HOME/ros-local-rpms" "$NAME"
n=0
for rpm in $(find /tmp/rpm-build/RPMS -name "*.rpm" 2>/dev/null | grep -vE "debuginfo|debugsource"); do
  cp -f "$rpm" "$HOME/ros-local-rpms/"; chmod 0644 "$HOME/ros-local-rpms/$(basename "$rpm")"; n=$((n+1)); echo "copied $(basename "$rpm")"
done
[ "$n" -gt 0 ] || { echo "NO RPM PRODUCED"; exit 1; }
