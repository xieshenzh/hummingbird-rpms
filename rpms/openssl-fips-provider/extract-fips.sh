#!/usr/bin/bash -e
PKGNAME=$1
ORIGINAL_PACKAGE_VERSION=$2
ORIGINAL_PACKAGE_RELEASE=$3

# args: build-V-R arch

if [ -z "${RPM_BUILD_ROOT}" ]; then
    echo >&2 "RPM_BUILD_ROOT is not set"
    exit 1
fi
if [ -z "${ORIGINAL_PACKAGE_VERSION}" ]; then
    echo >&2 "ORIGINAL_PACKAGE_VERSION is not set"
    exit 1
fi
if [ -z "${ORIGINAL_PACKAGE_RELEASE}" ]; then
    echo >&2 "ORIGINAL_PACKAGE_RELEASE is not set"
    exit 1
fi

PKG_ARCH=${RPM_ARCH}
if [ "${PKG_ARCH}" = "i386" ]; then
    PKG_ARCH=i686
fi

OVR=${ORIGINAL_PACKAGE_VERSION}-${ORIGINAL_PACKAGE_RELEASE}
DBGDIR=usr/lib/debug
DBGSRCDIR=usr/src/debug/${PKGNAME}-${OVR}.${RPM_ARCH}
DEBUGINFO=${RPM_BUILD_DIR}/debuginfo.list
DEBUGSOURCE=${RPM_BUILD_DIR}/debugsourcefiles.list

# Remove existing files if any
rm -fr ${RPM_BUILD_ROOT}/${DBGDIR}
rm -fr ${RPM_BUILD_ROOT}/usr/src/debug/*
> ${DEBUGINFO}
> ${DEBUGSOURCE}

# fips.so
mkdir extract
pushd extract

rpm2cpio ${RPM_BUILD_DIR}/${PKGNAME}-so-${OVR}.${PKG_ARCH}.rpm |cpio -id --quiet
rpm2cpio ${RPM_BUILD_DIR}/${PKGNAME}-so-debuginfo-${OVR}.${PKG_ARCH}.rpm |cpio -id --quiet
rpm2cpio ${RPM_BUILD_DIR}/${PKGNAME}-debugsource-${OVR}.${PKG_ARCH}.rpm |cpio -id --quiet
FIPS_SO=$(find usr -name fips.so)
cp -adt ${RPM_BUILD_ROOT} --parents ${FIPS_SO}
FIPS_SO_DBG=$(find usr -name fips.so-${OVR}.${RPM_ARCH}.debug)
cp -adt ${RPM_BUILD_ROOT} --parents ${FIPS_SO_DBG}

FIPS_DBG_ID=$(find -L usr -samefile ${FIPS_SO_DBG} -xtype l)
FIPS_DBG_ID_DIR=$(dirname ${FIPS_DBG_ID})
cp -adt ${RPM_BUILD_ROOT} --parents ${FIPS_DBG_ID_DIR}

cp -adt ${RPM_BUILD_ROOT} --parents usr/src/debug

popd

pushd ${RPM_BUILD_ROOT}

find ${DBGDIR} -type d | sed -e "s#^#%dir /#" >> ${DEBUGINFO}
find ${DBGDIR} -type f | sed -e "s#^#/#">> ${DEBUGINFO}
find ${DBGDIR} -type l | sed -e "s#^#/#">> ${DEBUGINFO}

find ${DBGSRCDIR} -type d | sed -e "s#^#%dir /#" >> ${DEBUGSOURCE}
find ${DBGSRCDIR} -type f | sed -e "s#^#/#">> ${DEBUGSOURCE}
find ${DBGSRCDIR} -type l | sed -e "s#^#/#">> ${DEBUGSOURCE}

popd
