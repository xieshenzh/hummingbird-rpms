#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/iptables/Regression/TRACE-target-of-iptables-can-t-work-in
#   Description: Test for TRACE target of iptables can't work in
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

prepare_page() {
    section=$1
    name=$2
    dest=${name}.manpage
    zcat /usr/share/man/man${section}/${name}.${section}.gz | tr -s ' ' > ${dest}
    rlAssertExists ${dest}
}

rlJournalStart
    rlPhaseStartSetup
        # rlAssertRpm kernel
        rlLogInfo $(uname -r)
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "pushd $TmpDir"
        prepare_page 8 iptables-extensions
        for svc in $SERVICES; do
            rlServiceStop $svc
        done
        rlRun "ip -4 -o r | grep default | head -1 | sed -re 's/.*dev ((\.|\w)+).*/\1/' > default-iface"
        IFACE="$(< default-iface)"
        rlAssertExists "/sys/class/net/$IFACE"
        rlRun "ip route save > ip-route.save" 0 "save routing info"
        rlRun "ip -6 route save > ip-route.save6" 0 "save ipv6 routing info"
        rlRun "ip -6 r add default dev $IFACE" 0,2 "add ipv6 default route"
        rlRun "rmmod nf_log_ipv4" 0,1
        rlRun "rmmod nf_log_ipv6" 0,1
    rlPhaseEnd

    rlPhaseStartTest "manpage check"
        rlAssertGrep "nfnetlink_log" iptables-extensions.manpage
        if rlIsRHEL 7 && rlIsRHEL '>=7.3' ; then
            # RHEL version-specific libxt_TRACE man page patchs
            rlAssertGrep "nf_log_ipv4(6)" iptables-extensions.manpage
            rlAssertNotGrep "ip(...)?t_LOG" iptables-extensions.manpage -Ei
        fi
    rlPhaseEnd

    ipv4_ping() {
        rlRun "ping -i 0.2 -c 3 -W 1 192.0.2.99" 0,1 "ipv4 icmp out (ping)"
    }
    ipv6_ping() {
        rlRun "ping6 -i 0.2 -c 3 -W 1 2001:DB8::99" 0,1 "ipv6 icmp out (ping6)"
    }
    get_messages() {
        if rlIsFedora; then
            journalctl -qkb
        else
            cat /var/log/messages
        fi
    }

    rlPhaseStartTest "iptables_TRACE"
        rlRun "get_messages > messages.log-orig"
        rlRun "iptables -t raw -I OUTPUT -p icmp -j TRACE" 0
        rlRun "ip6tables -t raw -I OUTPUT -p icmpv6 -j TRACE" 0
        if rlTestVersion "$(uname -r)" "<" "4.6"; then
            ipv4_ping; ipv6_ping
            rlRun "get_messages > messages.current"

            rlRun "diff messages.log-orig messages.current > diff.1" 0,1
            echo --debug_START--
            cat diff.1
            echo --debug_END--
            rlRun "modprobe nf_log_ipv4" 0 "load ipv4 TRACE logging module"
            rlRun "modprobe nf_log_ipv6" 0 "load ipv6 TRACE logging module"
            rlAssertNotGrep "TRACE" diff.1
        else
            rlLogInfo "new kernel detected: skipping loading modules and associated checks"
        fi
        ipv4_ping; ipv6_ping
        rlRun "get_messages > messages.current"

        rlRun "diff messages.log-orig messages.current > diff.2" 0,1
        rlAssertGrep "TRACE" diff.2
        rlAssertGrep "TRACE.*PROTO=ICMP " diff.2
        rlAssertGrep "TRACE.*PROTO=ICMPv6 " diff.2
        echo --debug_START--
        cat diff.2
        echo --debug_END--
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "ip route flush default" 0 "flush ip route data"
        rlRun "ip -6 route flush default" 0 "flush ipv6 route data"
        rlRun "ip route restore < ip-route.save" 0 "restore routing info"
        rlRun "ip -6 route restore < ip-route.save6" 0 "restore routing info ipv6"
        rlRun "iptables -t raw -F"
        rlRun "ip6tables -t raw -F"
        if rlTestVersion "$(uname -r)" "<" "4.6"; then
            rlRun "rmmod nf_log_ipv4"
            rlRun "rmmod nf_log_ipv6"
            rlRun "rmmod nf_log_common"
            rlRun "rmmod nfnetlink_log" 0,1
	else
            rlLogInfo "new kernel detected: skipping unloading modules"
	fi
        rlLogInfo "restoring services"
        for svc in $SERVICES; do
            rlServiceRestore $svc
        done
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
