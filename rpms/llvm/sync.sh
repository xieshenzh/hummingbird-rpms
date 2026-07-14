#!/bin/bash

# Synchronize code from Rawhide to Fedora compat or CentOS.

set -ex

CENTOS_REF=c10s
CENTOS_DIR=llvm-centos

COMPAT_REF=rawhide
COMPAT_DIR=llvm-compat

fedora_dir=llvm-fedora
fedora_ref=rawhide



function error() {
  echo "Error: $1" >&2
  exit 1
}

# Default parameter values.
bundle=1
initialize=0
target="centos"

script_dir=$(dirname $0)

while [ $# -gt 0 ]; do
  case $1 in
    --no-bundle )
      bundle=0
      ;;
    --initialize )
      initialize=1
      ;;
    --compat )
      target="compat"
      ;;
    --out-ref )
      shift
      out_ref=$1
      ;;
    --fedora-ref )
      shift
      fedora_ref=$1
      ;;
    * )
      echo "unknown option $1"
      exit 1
      ;;
  esac
  shift
done

if [[ "$target" == "centos" && initialize -eq 1 ]]; then
  error "cannot initialize CentOS repository."
fi

case "$target" in
  "centos" )
    out_ref=${out_ref:-$CENTOS_REF}
    out_dir=$CENTOS_DIR
    ;;
  "compat")
    out_ref=${out_ref:-$COMPAT_REF}
    out_dir=$COMPAT_DIR
    ;;
  * )
    echo "unknown target $target"
    exit 1
    ;;
esac

function centos_init() {
  git clone --depth 10 -b $out_ref https://gitlab.com/redhat/centos-stream/rpms/llvm.git $out_dir
}

function centos_post_sync() {
  if [ $bundle -eq 1 ]; then
    sed -i 's/^%bcond_with bundle_compat_lib$/%bcond_without bundle_compat_lib/g' $out_dir/llvm.spec
  fi

  case "$out_ref" in
    *rhel-8*)
      # RHEL-8 does not support rpmautospec completely.
      sed -i \
        -e 's/%autorelease/1%{?dist}/g' \
        -e 's/%{?autochangelog}//g' \
        -e 's/%{!?autochangelog:\(.*\)}/\1/g' \
        $out_dir/llvm.spec
      ;;
  esac

  for f in $out_dir/tests/*; do
    sed -i 's~https://src.fedoraproject.org/tests/llvm.git~https://gitlab.com/redhat/centos-stream/tests/llvm.git~g' $f
  done
}

function compat_init() {
  if [ $initialize -eq 1 ]; then
    test -d $out_dir || mkdir $out_dir
  else
    git clone --depth 10 -b $out_ref https://src.fedoraproject.org/rpms/llvm${maj_ver}.git $out_dir
  fi
}

function compat_post_sync() {
  sed -i 's/^%bcond_with compat_build$/%bcond_without compat_build/g' $out_dir/llvm.spec
  mv $out_dir/llvm.spec  $out_dir/llvm${maj_ver}.spec

  prev_ver=$(echo "$maj_ver - 1" | bc)
  # The test plans from Rawhide are not applicable to compat packages.
  # They also end up installing the default packages instead of compat.
  # We do not add them to .sync-ignore, because they are needed on CentOS.
  find "$out_dir/tests" -name '*.fmf' -delete
  # Get the files from the previous compat package and update them.
  for f in "gating.yaml" "tests/build-gating.fmf"; do
    # Warning: the way this script works and our documentation explains it is
    # counter intuitive. One may expect that rawhide provides the latest and
    # greatest files, but for the files in this loop they are actually
    # provided by the previous compat package.
    # Changes to these files in the current compat package may be overwritten
    # when this script is executed again. Luckily, by the time we distribute
    # the compat package in Fedora, upstream does not provide more updates,
    # meaning that we do not run this script on the same repository twice.
    curl -s --create-dirs \
      -o "$out_dir/$f" \
      "https://src.fedoraproject.org/rpms/llvm${prev_ver}/raw/rawhide/f/$f"

    sed -i\
      -e "s/llvm$prev_ver/llvm$maj_ver/g" \
      -e "s/\g<1>$prev_ver|/\g<1>$maj_ver|/g" \
      "$out_dir/$f"
  done
}



git clone --depth 10 -b $fedora_ref https://src.fedoraproject.org/rpms/llvm.git $fedora_dir
maj_ver=$(awk '/^%global maj_ver / { print $3 }' ${fedora_dir}/llvm.spec)

# Initialize the output directory.
${target}_init

rsync --exclude-from=$script_dir/.sync-ignore --delete --cvs-exclude -av $fedora_dir/ $out_dir/

# Apply changes after synchronizing the files.
${target}_post_sync
