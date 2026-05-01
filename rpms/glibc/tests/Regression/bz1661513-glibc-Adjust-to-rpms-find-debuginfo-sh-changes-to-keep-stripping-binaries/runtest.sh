#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /tools/glibc/Regression/bz1661513-glibc-Adjust-to-rpms-find-debuginfo-sh-changes-to-keep-stripping-binaries
#   Description: Test for BZ#1661513 (glibc: Adjust to rpm's find-debuginfo.sh changes, to keep stripping binaries)
#   Author: Martin Coufal <mcoufal@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2022 Red Hat, Inc.
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


rlJournalStart
    rlPhaseStartSetup

        rlRun "tmpdir=$(mktemp -d)"
        rlRun "pushd $tmpdir"
        # make sure glibc-debuginfo is not installed
        if rlCheckRpm glibc-debuginfo; then
            rlRun "dnf -y remove glibc-debuginfo"
        fi

    rlPhaseEnd

    rlPhaseStartTest

        # All programs (ldconfig, iconvconfig etc.) should be stripped, the dynamic loader (the target of the /usr/bin/ld.so symbolic link) should be unstripped
        rlRun "file /sbin/ldconfig /sbin/iconvconfig /usr/bin/localedef $(readlink -f /usr/bin/ld.so) > output.log 2>&1"
        rlAssertGrep "ldconfig.*, stripped" output.log
        rlAssertGrep "iconvconfig.*, stripped" output.log
        rlAssertGrep "localedef.*, stripped" output.log
        rlAssertGrep "$(readlink -f /usr/bin/ld.so).*, not stripped" output.log
        rlLogInfo "Content of output.log:\n$(cat output.log)"

        # some debugging info (e.g. pthread struct) should be accessible even without installed debuginfo packages
        rlRun "gdb --batch -ex 'ptype struct pthread' /usr/bin/ld.so > gdb.log 2>&1"
        rlAssertGrep "type = struct pthread" gdb.log
        rlAssertNotGrep "No struct type named pthread" gdb.log

    rlPhaseEnd

    rlPhaseStartCleanup

        rlRun "popd"
        rlRun "rm -rf $tmpdir"

    rlPhaseEnd
    rlJournalPrintText
rlJournalEnd
