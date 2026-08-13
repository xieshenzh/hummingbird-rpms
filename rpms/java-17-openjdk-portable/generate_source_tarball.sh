#!/bin/bash

# Copyright (C) 2024 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Generates the 'source tarball' for JDK projects.
#
# Example 1:
# When used from local repo set REPO_ROOT pointing to file:// with your repo.
# If your local repo follows upstream forests conventions, it may be enough to
# set OPENJDK_URL.
#
# Example 2:
# This will read the OpenJDK feature version from the spec file, then create a
# tarball from the most recent tag for that version in the upstream Git
# repository.
#
# $ OPENJDK_LATEST=1 ./generate_source_tarball.sh
# [...]
# Tarball is: temp-generated-source-tarball-ujD/openjdk-17.0.20+8.tar.xz
#
# Unless you use OPENJDK_LATEST, you have to set PROJECT_NAME, REPO_NAME and
# VERSION, e.g.:
#
# PROJECT_NAME=openjdk
# REPO_NAME=jdk17u
# VERSION=jdk-17.0.20+8
#
# or to e.g., prepare systemtap, icedtea7's jstack and other tapsets:
#
# VERSION=6327cf1cea9e
# REPO_NAME=icedtea7-2.6
# PROJECT_NAME=release
# OPENJDK_URL=http://icedtea.classpath.org/hg/
# TO_COMPRESS="*/tapset"
#
# They are used to create correct name and are used in construction of sources
# URL (unless REPO_ROOT is set).
#
# This script creates a single source tarball out of the repository based on the
# given tag and removes code not allowed in Fedora/RHEL.

set -e

OPENJDK_URL_DEFAULT=https://github.com
COMPRESSION_DEFAULT=xz

if [ "$1" = "help" ] ; then
    echo "Behaviour may be specified by setting the following variables:"
    echo
    echo "VERSION        - the version of the specified OpenJDK project"
    echo "                 (required unless OPENJDK_LATEST is set)"
    echo "PROJECT_NAME   - the name of the OpenJDK project being archived"
    echo "                 (needed to compute REPO_ROOT and/or"
    echo "                  FILE_NAME_ROOT automatically;"
    echo "                  optional if they are set explicitly)"
    echo "REPO_NAME      - the name of the OpenJDK repository"
    echo "                 (needed to compute REPO_ROOT automatically;"
    echo "                  optional if REPO_ROOT is set explicitly)"
    echo "OPENJDK_URL    - the URL to retrieve code from"
    echo "                 (defaults to ${OPENJDK_URL_DEFAULT})"
    echo "COMPRESSION    - the compression type to use"
    echo "                 (defaults to ${COMPRESSION_DEFAULT})"
    echo "FILE_NAME_ROOT - name of the archive, minus extensions"
    echo "                 (defaults to PROJECT_NAME-VERSION)"
    echo "REPO_ROOT      - the location of the Git repository to archive"
    echo "                 (defaults to OPENJDK_URL/PROJECT_NAME/REPO_NAME.git)"
    echo "TO_COMPRESS    - what part of clone to pack"
    echo "                 (defaults to ${VERSION})"
    echo "BOOT_JDK       - the bootstrap JDK to satisfy the configure run"
    echo "                 (defaults to packaged JDK version)"
    echo "WITH_TEMP      - run in a temporary directory"
    echo "                 (defaults to disabled)"
    echo "OPENJDK_LATEST - deduce VERSION from most recent upstream tag"
    echo "                 (implies WITH_TEMP, computes everything else"
    echo "                  automatically; Note: accesses network to read"
    echo "                  tag list from remote Git repository)"
    exit 1;
fi

if [ "$OPENJDK_LATEST" != "" ] ; then
    FEATURE_VERSION=$(echo '%featurever' \
                          | rpmspec --shell ./*.spec 2>/dev/null \
                          | grep --after-context 1 featurever \
                          | tail --lines 1)
    PROJECT_NAME=openjdk
    REPO_NAME=jdk"${FEATURE_VERSION}"u
    # Skip -ga tags since those are the same as the most recent non-ga tag, and
    # the non-ga tag is the one that is used to generated the official source
    # tarball.  For example:
    # ca760c86642aa2e0d9b571aaabac054c0239fbdc  refs/tags/jdk-17.0.10-ga^{}
    # 25a2e6c20c9a96853714284cabc6b456eb095070  refs/tags/jdk-17.0.10-ga
    # ca760c86642aa2e0d9b571aaabac054c0239fbdc  refs/tags/jdk-17.0.10+7^{}
    # e49c5749b10f3e90274b72e9279f794fdd191d27  refs/tags/jdk-17.0.10+7
    VERSION=$(git ls-remote --tags --refs --sort=-version:refname \
                  "${OPENJDK_URL_DEFAULT}/${PROJECT_NAME}/${REPO_NAME}.git" \
                  "jdk-${FEATURE_VERSION}*" \
                  | grep --invert-match '\-ga$' \
                  | head --lines 1 | cut --characters 52-)
    FILE_NAME_ROOT=open${VERSION}
    WITH_TEMP=1
fi

if [ "$WITH_TEMP" != "" ] ; then
    pushd "$(mktemp --directory temp-generated-source-tarball-XXX)"
fi

if [ "$VERSION" = "" ] ; then
    echo "No VERSION specified"
    exit 2
fi
echo "Version: ${VERSION}"

NUM_VER=${VERSION##jdk-}
RELEASE_VER=${NUM_VER%%+*}
BUILD_VER=${NUM_VER##*+}
MAJOR_VER=${RELEASE_VER%%.*}
echo "Major version is ${MAJOR_VER}, release ${RELEASE_VER}, build ${BUILD_VER}"

if [ "$BOOT_JDK" = "" ] ; then
    echo "No boot JDK specified".
    BOOT_JDK=/usr/lib/jvm/java-${MAJOR_VER}-openjdk;
    echo -n "Checking for ${BOOT_JDK}...";
    if [ -d "${BOOT_JDK}" ] && [ -x "${BOOT_JDK}"/bin/java ] ; then
        echo "Boot JDK found at ${BOOT_JDK}";
    else
        echo "Not found";
        PREV_VER=$((MAJOR_VER - 1));
        BOOT_JDK=/usr/lib/jvm/java-${PREV_VER}-openjdk;
        echo -n "Checking for ${BOOT_JDK}...";
        if [ -d ${BOOT_JDK} ] && [ -x ${BOOT_JDK}/bin/java ] ; then
            echo "Boot JDK found at ${BOOT_JDK}";
        else
            echo "Not found";
            exit 4;
        fi
    fi
else
    echo "Boot JDK: ${BOOT_JDK}";
fi

if [ "$OPENJDK_URL" = "" ] ; then
    OPENJDK_URL=${OPENJDK_URL_DEFAULT}
    echo "No OpenJDK URL specified; defaulting to ${OPENJDK_URL}"
else
    echo "OpenJDK URL: ${OPENJDK_URL}"
fi

if [ "$COMPRESSION" = "" ] ; then
    # rhel 5 needs tar.gz
    COMPRESSION=${COMPRESSION_DEFAULT}
fi
echo "Creating a tar.${COMPRESSION} archive"

if [ "$FILE_NAME_ROOT" = "" ] ; then
    if [ "$PROJECT_NAME" = "" ] ; then
        echo "No PROJECT_NAME specified, needed by FILE_NAME_ROOT"
        exit 1
    fi
    FILE_NAME_ROOT=${PROJECT_NAME}-${VERSION}
    echo "No file name root specified; default to ${FILE_NAME_ROOT}"
fi
if [ "$REPO_ROOT" = "" ] ; then
    if [ "$PROJECT_NAME" = "" ] ; then
        echo "No PROJECT_NAME specified, needed by REPO_ROOT"
        exit 1
    fi
    if [ "$REPO_NAME" = "" ] ; then
        echo "No REPO_NAME specified, needed by REPO_ROOT"
        exit 3
    fi
    REPO_ROOT="${OPENJDK_URL}/${PROJECT_NAME}/${REPO_NAME}.git"
    echo "No repository root specified; default to ${REPO_ROOT}"
fi;

if [ "$TO_COMPRESS" = "" ] ; then
    TO_COMPRESS="${VERSION}"
    echo "No targets to be compressed specified ; default to ${TO_COMPRESS}"
fi;

echo -e "Settings:"
echo -e "\tVERSION: ${VERSION}"
echo -e "\tPROJECT_NAME: ${PROJECT_NAME}"
echo -e "\tREPO_NAME: ${REPO_NAME}"
echo -e "\tOPENJDK_URL: ${OPENJDK_URL}"
echo -e "\tCOMPRESSION: ${COMPRESSION}"
echo -e "\tFILE_NAME_ROOT: ${FILE_NAME_ROOT}"
echo -e "\tREPO_ROOT: ${REPO_ROOT}"
echo -e "\tTO_COMPRESS: ${TO_COMPRESS}"
echo -e "\tBOOT_JDK: ${BOOT_JDK}"

if [ -d "${FILE_NAME_ROOT}" ] ; then
    echo "exists exists exists exists exists exists exists "
    echo "reusing reusing reusing reusing reusing reusing "
    echo "${FILE_NAME_ROOT}"
    STAT_TIME="$(stat --format=%Y "${FILE_NAME_ROOT}")"
    TAR_TIME="$(date --date=@"${STAT_TIME}" --iso-8601=seconds)"
else
    mkdir "${FILE_NAME_ROOT}"
    pushd "${FILE_NAME_ROOT}"
        echo "Cloning ${VERSION} root repository from ${REPO_ROOT}"
        git clone --depth=1 -b "${VERSION}" "${REPO_ROOT}" "${VERSION}"
        pushd "${VERSION}"
            TAR_TIME="$(git log --max-count 1 --format=%cI)"
        popd
    popd
fi
pushd "${FILE_NAME_ROOT}"
    # Generate .src-rev so build has knowledge of the revision the tarball was
    # created from
    mkdir build
    pushd build
        sh "${PWD}"/../"${VERSION}"/configure --with-boot-jdk="${BOOT_JDK}"
        make store-source-revision
    popd
    rm -rf build

    EA_PART="$(awk -F= \
               '/^DEFAULT_PROMOTED_VERSION_PRE/ { if ($2) print "-"$2 }' \
               "${VERSION}"/make/conf/version-numbers.conf)"
    TARBALL_BASE=${FILE_NAME_ROOT}${EA_PART}.tar
    pushd "${VERSION}"
        # Omit commit checks, history, and GHA from archive.
        for skip in .jcheck .hgtags .hgignore .gitattributes .gitignore .github
        do
            echo "${skip}"" export-ignore" >> .git/info/attributes
        done
        # Do not bother with --mtime here; specify it to tar below.
        # Unforunately, git-archive sorts added files like .src-rev at the end;
        # retar below to use GNU tar --sort=name ordering which sorts .src-rev
        # at the start.
        git archive --output "${TARBALL_BASE}" --prefix="${VERSION}"/ \
            --add-file=.src-rev --format=tar "${VERSION}"
    popd
    mv "${VERSION}" "${VERSION}".git
    tar xf "${VERSION}".git/"${TARBALL_BASE}"
    echo "Compressing remaining forest"
    if [ "$COMPRESSION" = "xz" ] ; then
        SWITCH=cJf
    else
        SWITCH=czf
    fi
    TARBALL_NAME=`echo ${TARBALL_BASE}.${COMPRESSION} | sed "s/-jdk-/-/g"`
    XZ_OPT=${XZ_OPT-"-T0"} \
          tar --mtime="${TAR_TIME}" --owner=root --group=root --sort=name \
          -$SWITCH "${TARBALL_NAME}" "${TO_COMPRESS}"
    mv "${TARBALL_NAME}" ..
popd
if [ "$WITH_TEMP" != "" ] ; then
    echo "Tarball is: $(realpath --relative-to=.. .)/${TARBALL_NAME}"
    popd
else
    echo -n "Done. You may want to remove the uncompressed version"
    echo " - $FILE_NAME_ROOT"
fi

# Local Variables:
# compile-command: "shellcheck generate_source_tarball.sh"
# fill-column: 80
# indent-tabs-mode: nil
# sh-basic-offset: 4
# End:
