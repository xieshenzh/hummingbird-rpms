#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/iptables-rule-deletion-fails-for-rules-that-use
#   Description: Test for iptables rule deletion fails for rules that use
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

IPSET4="ipsetv4"
IPSET6="ipsetv6"

rlJournalStart
    rlPhaseStartSetup
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        rlRun "ipset create $IPSET4 hash:ip"
        rlRun "ipset create $IPSET6 hash:ip family inet6"
        rlRun "iptables-save -t mangle > ipt4.save"
        rlRun "ip6tables-save -t mangle > ipt6.save"
    rlPhaseEnd

    rlPhaseStartTest
        RULE40="-A PREROUTING -m set --match-set $IPSET4 dst -j ACCEPT"
        RULE40d="-D PREROUTING -m set --match-set $IPSET4 dst -j ACCEPT"
        RULE41="-A PREROUTING -m set --match-set $IPSET4 dst -j SET --add-set $IPSET4 src"
        RULE41d="-D PREROUTING -m set --match-set $IPSET4 dst -j SET --add-set $IPSET4 src"
        RULE60="-A PREROUTING -m set --match-set $IPSET6 dst -j ACCEPT"
        RULE60d="-D PREROUTING -m set --match-set $IPSET6 dst -j ACCEPT"
        RULE61="-A PREROUTING -m set --match-set $IPSET6 dst -j SET --add-set $IPSET6 src"
        RULE61d="-D PREROUTING -m set --match-set $IPSET6 dst -j SET --add-set $IPSET6 src"
        for RULE in "$RULE40" "$RULE40d" "$RULE41" "$RULE41d"; do
            rlRun "iptables -t mangle $RULE"
        done
        for RULE in "$RULE60" "$RULE60d" "$RULE61" "$RULE61d"; do
            rlRun "ip6tables -t mangle $RULE"
        done
        rlRun "iptables-save -t mangle > ipt4.save2"
        rlRun "ip6tables-save -t mangle > ipt6.save2"
        rlRun "sed -e '/^#/d' -e 's/\[.*:.*\]$//' -i ipt4* ipt6*" 0 "magically unify savefiles"
        rlAssertNotDiffer ipt4.save ipt4.save2
        rlAssertNotDiffer ipt6.save ipt6.save2
        diff -u ipt4.save ipt4.save2
        diff -u ipt6.save ipt6.save2
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "ipset destroy $IPSET4"
        rlRun "ipset destroy $IPSET6"
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
