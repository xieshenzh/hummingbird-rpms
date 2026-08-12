#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Sanity/backport-iptables-add-libxt-cgroup-frontend
#   Description: Test for backport iptables add libxt_cgroup frontend
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

CGNUM="15"
CGNAME="15"
CGDIR="/sys/fs/cgroup/net_cls/$CGNAME"
DEST_IP4="192.0.2.99" # TEST-NET-1
DEST_IP42="192.0.2.199" # TEST-NET-1
DEST_IP6="2001:0db8:0000:0000:0000:0000:0000:abc0" #has to be expanded due to matching !
DEST_IP62="2001:0db8:0000:0000:0000:0000:0000:abc1"
SKIP6=false

rlJournalStart
    rlPhaseStartSetup
        # rlAssertRpm kernel-$(uname -r)
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        if rlIsRHEL '>=7'; then
            rlServiceStop firewalld
            sleep 1
        fi
        rlLogInfo "check if net_cls cgroup is present"
        rlAssertGrep "cgroup.*net_cls" /proc/mounts
        rlRun "cgcreate -g net_cls:$CGNAME" 0 "create cgroup '15'"
        rlRun "echo $CGNUM > $CGDIR/net_cls.classid" 0 "assign numerical id to cgroup"
    rlPhaseEnd

    rlPhaseStartTest
        ping -W 1 -c 30 $DEST_IP4 &
        PING4_P1=$! EC4=$?
        ping -W 1 -c 30 $DEST_IP42 &
        PING4_P2=$! EC42=$?
        rlRun "[[ $EC4 -eq 0 && $EC42 -eq 0 ]]" 0 "ping ipv4 running to $DEST_IP4, $DEST_IP42"

        ping6 -W 1 -c 30 $DEST_IP6 &
        PING6_P1=$! EC6=$?
        sleep 1
        if [[ $EC6 -eq 2 ]] || ! kill -0 $PING6_P1 2>/dev/null; then
            rlLogInfo "skipping ipv6 test, network stack unavailable"
            SKIP6=true
        else
            ping6 -W 1 -c 30 $DEST_IP62 &
            PING6_P2=$!
            rlRun "kill -0 $PING6_P1 && kill -0 $PING6_P2" 0 "ping ipv6 running to $DEST_IP6, $DEST_IP62"
        fi
        journalctl -fkb > dmesg.out &
        DMESG_P=$!
        echo > dmesg.out # clear dmesg out

        rlRun "iptables -A OUTPUT -m cgroup --cgroup $CGNUM -j LOG"
        rlRun "ip6tables -A OUTPUT -m cgroup --cgroup $CGNUM -j LOG"

        rlRun "echo $PING4_P2 >> $CGDIR/tasks" 0 "Add second ping to cgroup '15'"
        $SKIP6 || rlRun "echo $PING6_P2 >> $CGDIR/tasks" 0 "Add second ping6 to cgroup '15'"
        cat $CGDIR/tasks
        sleep 10
        cat dmesg.out
        rlAssertGrep "$DEST_IP42" dmesg.out
        $SKIP6 || rlAssertGrep "$DEST_IP62" dmesg.out
        rlAssertNotGrep "$DEST_IP4" dmesg.out
        rlAssertNotGrep "$DEST_IP6" dmesg.out
    rlPhaseEnd

    rlPhaseStartCleanup
        kill $DMESG_P
        # pings die after 30s of execution either way
        kill $PING4_P1
        kill $PING4_P2
        $SKIP6 || kill $PING6_P1
        $SKIP6 || kill $PING6_P2
        sleep 1

        rlRun "iptables -F" 0 "cleanup iptables"
        rlRun "ip6tables -F" 0 "cleanup ip6tables"
        rlServiceRestore firewalld
        rlRun "cgdelete -g net_cls:$CGNAME" 0 "delete cgroup"
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
