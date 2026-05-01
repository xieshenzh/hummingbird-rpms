#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /tools/glibc/Regression/bz1430477-glibc-Missing-else-branch-in-libc-calloc
#   Description: Test for BZ#1430477 (glibc Missing else branch in __libc_calloc)
#   Author: Sergey Kolosov <skolosov@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2017 Red Hat, Inc.
#
#   This program is free software: you can redistribute it and/or
#   modify it under the terms of the GNU General Public License as
#   published by the Free Software Foundation, either version 2 of
#   the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be
#   useful, but WITHOUT ANY WARRANTY; without even the implied
#   warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
#   PURPOSE.  See the GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program. If not, see http://www.gnu.org/licenses/.
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# Include Beaker environment
. /usr/share/beakerlib/beakerlib.sh || exit 1

PACKAGE="glibc"

rlJournalStart
    rlPhaseStartSetup
        PRARCH="$(rlGetPrimaryArch)"
        BUILDDIR="$(rpm -E '%{_builddir}')"
        SPECDIR="$(rpm -E '%{_specdir}')"
        rlAssertRpm $PACKAGE
        rlLog "Build directory: $BUILDDIR"
        rlLog "Spec directory:  $SPECDIR"
        rlLog "Architecture :  $PRARCH"

        rlLog "Cleaning build and spec directories of glibc files"
        rlRun "rm -rf $BUILDDIR/glibc*" 0 "Cleaning $BUILDDIR/glibc*"
        rlRun "rm -rf $SPECDIR/glibc*.spec" 0 "Cleaning $SPECDIR/glibc*.spec"
        rlRun "rm -rf glibc*.src.rpm" 0 "Removing any present glibc src.rpm"

        rlLog "Installing glibc srpm"
        rlFetchSrcForInstalled $PACKAGE
        rlRun "rpm -Uhv $PACKAGE*.src.rpm"
        rlAssertExists $SPECDIR/$PACKAGE.spec

        rlRun "dnf builddep -y $PACKAGE-*.src.rpm" 0 "Installing dependences"
    rlPhaseEnd

    rlPhaseStartTest "Building glibc"
        rlRun "rpmbuild -bc ${SPECDIR}/${PACKAGE}.spec &> glibc_build_log.txt" 0 "Unpacking $PACKAGE"
        ISSUCCESS=$?
        if [ $ISSUCCESS -ne 0 ]
        then
            rlFileSubmit glibc_build_log.txt
            rlFail "Glibc compilation error"
        fi

        if rlIsRHEL "==10"; then
            BUILDS="$BUILDDIR/glibc-2.39/build*"
        elif rlIsFedora ">=41"; then
            BUILDS="$BUILDDIR/glibc*build/glibc*/build*"
        else
            BUILDS="$BUILDDIR/glibc*/build*"
        fi
        rlLog "Found builds at:"
        for build in $BUILDS; do
            rlLog "$build"
        done; unset build
    rlPhaseEnd

    rlPhaseStartTest "Check for uninitialized values"
        for CURBUILD in $BUILDS
        do
            rlRun -c "pushd $CURBUILD"
            rlRun -c "rm  malloc/malloc.o"
            rlRun -c "make -r PARALLELMFLAGS="" -C .. -C malloc objdir=`pwd` subdir=malloc &> malloc_build_log.txt"
            rlAssertExists malloc_build_log.txt
            rlAssertNotGrep "‘oldtop’ may be used uninitialized in this function" malloc_build_log.txt
            rlAssertNotGrep "‘oldtopsize’ may be used uninitialized in this function" malloc_build_log.txt
            rlFileSubmit malloc_build_log.txt ${CURBUILD}_malloc_build_log
            rlRun -c "popd"
        done
    rlPhaseEnd

    rlPhaseStartCleanup
        if [ -n "$KEEP_GLIBC_RESULTS" ]; then
            rlLog "$(pwd) contains:"
            rlLog "$(ls $(pwd))"
            rlLog "Build Directory at: $(ls $BUILDDIR)"
            rlLog "Spec File at: $(ls $SPECDIR/glibc*.spec)"
        else
            rlRun "rm glibc*.src.rpm"
            rlRun "rm -rf $BUILDDIR/glibc* $SPECDIR/glibc*.spec"
        fi
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
