#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/iptables-save-cuts-space-before-j
#   Description: Test for iptables-save cuts space before -j
#   Author: Tomas Dolezal <todoleza@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2015 Red Hat, Inc.
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
. /usr/bin/rhts-environment.sh || exit 1
. /usr/share/beakerlib/beakerlib.sh || exit 1

rlJournalStart
    rlPhaseStartSetup
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        rlServiceStart iptables
    rlPhaseEnd

    rlPhaseStartTest
        RULE="-A INPUT -p dccp -m dccp --dccp-type RESET,INVALID -j LOG"
        if rlIsRHEL '>6' || rlIsFedora; then
            RULE="${RULE/type/types}" # it is exported under other name
        fi
        rlLogInfo "using rule '$RULE'"
        rlRun "iptables $RULE" 0 "add rule for ipv4"
        rlRun "ip6tables $RULE" 0 "add rule for ipv6"
        rlRun "iptables-save | grep -- '$RULE'" 0 "check rule for ipv4"
        rlRun "ip6tables-save | grep -- '$RULE'" 0 "check rule for ipv6"
    rlPhaseEnd

    rlPhaseStartCleanup
        rlServiceStop iptables
        rlServiceRestore iptables
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
