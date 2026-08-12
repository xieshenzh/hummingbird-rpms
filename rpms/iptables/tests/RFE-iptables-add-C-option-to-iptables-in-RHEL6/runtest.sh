#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/RFE-iptables-add-C-option-to-iptables-in-RHEL6
#   Description: Test for RFE iptables add -C option to iptables in RHEL6 to
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

TESTD=$PWD

rlJournalStart
    rlPhaseStartSetup
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        rlRun "source $TESTD/rules.in" 0 "read ruleset"
        rlRun "iptables -F"
        rlRun "ip6tables -F"
    rlPhaseEnd

    rlPhaseStartTest
        declare -i sane=0
        for i in ${!rules4[*]}; do
            let sane++
            rlRun "iptables ${rules4[$i]}"
            testrule="${rules4[$i]/-A/-C}"
            rlRun "iptables $testrule"
        done
        for i in ${!rules6[*]}; do
            let sane++
            rlRun "ip6tables ${rules6[$i]}"
            testrule="${rules6[$i]/-A/-C}"
            rlRun "ip6tables $testrule"
        done
        #check itercount
        if [[ $sane -lt 40 ]]; then
            rlFail "test insane, do inspect" # rules were not properly loaded!
        fi
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "iptables -F"
        rlRun "iptables -t nat -F"
        rlRun "ip6tables -F"
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
