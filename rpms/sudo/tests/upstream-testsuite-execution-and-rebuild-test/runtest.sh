#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/sudo/Sanity/upstream-testsuite-execution-and-rebuild-test
#   Description: This test rebuild sudo source rpm and checks that rebuild is OK. The second - main - part is about upstream testsuite execution.
#   Author: Ales Marecek <amarecek@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2013 Red Hat, Inc. All rights reserved.
#
#   This copyrighted material is made available to anyone wishing
#   to use, modify, copy, or redistribute it subject to the terms
#   and conditions of the GNU General Public License version 2.
#
#   This program is distributed in the hope that it will be
#   useful, but WITHOUT ANY WARRANTY; without even the implied
#   warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
#   PURPOSE. See the GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public
#   License along with this program; if not, write to the Free
#   Software Foundation, Inc., 51 Franklin Street, Fifth Floor,
#   Boston, MA 02110-1301, USA.
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# Include Beaker environment
. /usr/bin/rhts-environment.sh || exit 1
. /usr/share/beakerlib/beakerlib.sh || exit 1

PACKAGE="sudo"
_SPEC_DIR="$(rpm --eval=%_specdir)"
_BUILD_DIR="$(rpm --eval=%_builddir)"
_LOG_REBUILD_F="${PACKAGE}-rebuild.log"
_LOG_TESTSUITE_F="${PACKAGE}-testsuite.log"


rlJournalStart
    rlPhaseStartSetup
        rlAssertRpm $PACKAGE
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
	# Source package is needed for code inspection
	rlFetchSrcForInstalled "${PACKAGE}" || yumdownloader --source "${PACKAGE}"
    rlRun "find . -size 0 -delete" 0 "Remove empty src.rpm-s"
	rlRun "yum-builddep -y --nogpgcheck ${PACKAGE}-*.src.rpm" 0 "Installing build dependencies"
	[ -d ${_BUILD_DIR} ] && rlRun "rm -rf ${_BUILD_DIR}/*" 0 "Cleaning build directory"
	rlRun "rpm -ivh ${PACKAGE}-*.src.rpm" 0 "Installing source rpm"
    rlPhaseEnd

    rlPhaseStartTest
	rlRun "QA_RPATHS=0x0002 rpmbuild -ba ${_SPEC_DIR}/${PACKAGE}.spec" 0 "Test: Rebuild of source '${PACKAGE}' package"
	rlGetPhaseState
	if [ $? -eq 0 ]; then
		cd ${_BUILD_DIR}/${PACKAGE}-*
		rlRun -s "make check" 0 "Test: Upstream testsuite"
		cd ${TmpDir}
		while read -r I; do
		    if [[ "$I" =~ $(echo '([^:]+): .+ tests run, .+ errors, (.*)% success rate') ]]; then
		        [[ "${BASH_REMATCH[2]}" == "100" ]]
		        rlAssert0 "Test: Checking tests of '${BASH_REMATCH[1]}'" $?
		    elif [[ "$I" =~ $(echo "([^:]+): .+ tests passed; (.+)/.+ tests failed") ]]; then
		        [[ "${BASH_REMATCH[2]}" == "0" ]]
		        rlAssert0 "Test: Checking tests of '${BASH_REMATCH[1]}'" $?
		    fi
		done < $rlRun_LOG
		rm -f $rlRun_LOG
	else
		rlFail "Skipping testsuite part because rebuild part failed."
	fi
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
