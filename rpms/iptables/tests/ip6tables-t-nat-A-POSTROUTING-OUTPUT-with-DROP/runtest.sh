#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/ip6tables-t-nat-A-POSTROUTING-OUTPUT-with-DROP
#   Description: Test for ip6tables -t nat -A POSTROUTING/OUTPUT with DROP
#   Author: Tomas Dolezal <todoleza@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2016 Red Hat, Inc.
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

SERVICES="iptables ip6tables firewalld"

rlJournalStart
    rlPhaseStartSetup
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        for svc in $SERVICES; do
            rlServiceStop $svc
        done
        rlRun "iptables -t nat -F"
        rlRun "ip6tables -t nat -F"
    rlPhaseEnd

    rlPhaseStartTest
        table="nat"
        assert_string="nat.*intended.*inhibited"
        for chain in PREROUTING INPUT OUTPUT POSTROUTING; do
            rlLogInfo "checking chain $chain"
            rlRun "iptables -t $table -A $chain -p icmp -j DROP 2>iptables.stderr" 2 \
                "iptables: Failure to accept DROP to '$table/$chain' chain"
            rlRun "ip6tables -t $table -A $chain -p icmpv6 -j DROP 2>ip6tables.stderr" 2 \
                "ip6tables: Failure to accept DROP to '$table/$chain' chain"
            rlAssertGrep "$assert_string" iptables.stderr -E
            rlAssertGrep "$assert_string" ip6tables.stderr -E
            rm -f iptables.stderr ip6tables.stderr
            echo --debug_START--
            set -x
            iptables-save | grep -E '\*|icmp'
            ip6tables-save | grep -E '\*|icmp'
            set +x
            echo --debug_END--
        done
        rlRun "iptables-save > ipt4.out"
        rlRun "ip6tables-save > ipt6.out"
        rlAssertNotGrep "icmp" ipt4.out
        rlAssertNotGrep "icmp" ipt6.out
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "iptables -t nat -F"
        rlRun "ip6tables -t nat -F"
        rlLogInfo "restoring services"
        for svc in $SERVICES; do
            rlServiceRestore $svc
        done
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
