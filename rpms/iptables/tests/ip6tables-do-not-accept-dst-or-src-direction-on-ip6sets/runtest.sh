#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/ip6tables-do-not-accept-dst-or-src-direction-on-ip6sets
#   Description: Test for while adding iptables rules with ipv6 sets in
#   Author: Tomas Dolezal <todoleza@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2014 Red Hat, Inc.
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
        rlRun "ip6tables-save > ip6tables.backup"
        rlRun "iptables-save > iptables.backup"
        rlRun "ip link add dev testbr type bridge" 0 "create bridge iface"
    rlPhaseEnd

    rlPhaseStartTest
        rlRun "ipset create ipsetv6 hash:net timeout 60 family inet6" 0 "Create hash:net ipset for ipv6"
        rlRun "ipset create ipsetv4 hash:net timeout 60 family inet" 0  "Create hash:net ipset for ipv4"
        rlRun "ipset list ipsetv6" 0 "verify ipsetv6 presence"
        rlRun "ipset list ipsetv4" 0 "verify ipsetv4 presence"
#        echo waiting; read; echo cont
        checkRule() {
            binary="$1"
            comment="$2"
            rlRun "$binary -t mangle $RULE" 0 "$comment"
            rlRun "$binary-save | grep -qe '$RULE'" 0 "verify rule"
        }
        for i in dst src dst,src src,dst; do
            # 6,4 (+)
            RULE="-A PREROUTING -i testbr -m set --match-set ipsetv6 $i -j ACCEPT"
            checkRule ip6tables "[ipv6] direction: $i. adding ip6tables rule to match set"
            RULE="-A PREROUTING -i testbr -m set --match-set ipsetv4 $i -j ACCEPT"
            checkRule iptables  "[ipv4] direction: $i. adding iptables rule to match set"

            # 6,4 (-)
            RULE="-A PREROUTING -i testbr -m set ! --match-set ipsetv6 $i -j ACCEPT"
            checkRule ip6tables "[ipv6] direction: $i. adding negated ip6tables rule to match set"
            RULE="-A PREROUTING -i testbr -m set ! --match-set ipsetv4 $i -j ACCEPT"
            checkRule iptables  "[ipv4] direction: $i. adding negated iptables rule to match set"
        done
        ip6tables-save
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "ip6tables -t mangle -F"
        rlRun "iptables -t mangle -F"
        rlRun "ip6tables-restore < ip6tables.backup"
        rlRun "iptables-restore < iptables.backup"
        rlRun "ip link set down dev testbr"
        rlRun "ip link del testbr" 0 "remove bridge iface"
        rlRun "ipset destroy ipsetv6" 0 "remove ipv6 ipset"
        rlRun "ipset destroy ipsetv4" 0 "remove ipv4 ipset"
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
