#!/usr/bin/bash -e

# args: package version release
PKGNAME=$1
VERSION=$2
RELEASE=$3
OVR="${VERSION}-${RELEASE}"
rpm2cpio ${PKGNAME}-${OVR}.src.rpm |cpio -id

rm -fr openssl-${VERSION}
tar --no-same-owner -xf openssl-${VERSION}-hobbled.tar.gz
rm ${PKGNAME}.spec

pushd openssl-${VERSION}
git init
git config user.email "openssl-fips-provider-build@redhat.com"
git config user.name "openssl-fips-provider build"
git add .
git commit -m "init commit" --quiet
git apply -p1 ../*.patch

cp ../ec_curve.c crypto/ec/
cp ../ectest.c test/
