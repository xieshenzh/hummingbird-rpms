#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Sanity/NFQUEUE-queue-bypass
#   Description: Test for "--queue-bypass" backport
#   Author: Ales Zelinka <azelinka@redhat.com>
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

PACKAGE="iptables"

rlJournalStart

    rlPhaseStartTest control-ping
         rlRun "ping -w 2 -c 2 127.0.0.1"
    rlPhaseEnd

    rlPhaseStartTest NFQUEUE-no-listener
         rlRun "iptables -I INPUT -p icmp -j NFQUEUE" 0 "queue all icmp for userspace processing"
         rlRun "ping -w 2 -c 2 127.0.0.1" 1-255 "ping 127.0.0.1 - none is listening on queue so packets will be dropped"
         rlRun "iptables -D INPUT -p icmp -j NFQUEUE" 0 "removing the queue rule"
    rlPhaseEnd

    rlPhaseStartTest NFQUEUE-no-listener-bypass
         rlRun "iptables -I INPUT -p icmp -j NFQUEUE --queue-bypass" 0 "queue all icmp for userspace processing, bypass if no one is listening"
         rlRun "ping -w 2 -c 2 127.0.0.1" 0 "ping 127.0.0.1 - none is listening on queue - bypass will make packets go through"
         rlRun "iptables -D INPUT -p icmp -j NFQUEUE --queue-bypass" 0 "removing the queue rule"
    rlPhaseEnd

rlJournalPrintText
rlJournalEnd
