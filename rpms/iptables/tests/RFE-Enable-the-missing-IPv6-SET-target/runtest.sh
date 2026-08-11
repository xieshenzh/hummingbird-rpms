#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/RFE-Enable-the-missing-IPv6-SET-target
#   Description: Test for [RFE] Enable the missing IPv6 "SET" target
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

IPSET=testset6

rlJournalStart
    rlPhaseStartSetup
        # rlAssertRpm kernel
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        rlRun "ipset create $IPSET hash:ip family inet6"
        rlRun "ipset add testset6 1234::3456"
        rlRun "ip6tables-save -t filter > ipt6.save"
    rlPhaseEnd

    rlPhaseStartTest
        RULE1="INPUT -p tcp -m multiport --dports 21,22,23,25,53,81,123,143 -m conntrack --ctstate NEW --syn -m set ! --match-set $IPSET src -j LOG --log-prefix 'LOG:IPSET added to $IPSET'"
        RULE2="INPUT -p tcp -m multiport --dports 21,22,23,25,53,81,123,143 -m conntrack --ctstate NEW --syn -m set ! --match-set $IPSET src -j SET --add-set $IPSET src"
        for op in -A -C -D; do #add, check, delete
            rlRun "ip6tables $op $RULE1" 0 "do $op logrule"
            rlRun "ip6tables $op $RULE2" 0 "do $op -j SET rule"
        done
        rlRun "ip6tables-save -t filter > ipt6.save2"
        rlRun "sed -e '/^#/d' -e 's/\[.*:.*\]$//' -i ipt6*" 0 "magically unify savefiles"
        rlAssertNotDiffer ipt6.save ipt6.save2
        diff -u ipt6.save ipt6.save2
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "ipset destroy $IPSET"
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
