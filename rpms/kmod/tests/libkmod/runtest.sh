#!/bin/bash
# SPDX-License-Identifier: LGPL-2.1+
# ~~~
#   runtest.sh of libkmod
#   Description: Tests for libkmod.
#
#   Author: Susant Sahani <susant@redhat.com>
#   Copyright (c) 2018 Red Hat, Inc.
# ~~~

# Include Beaker environment
. /usr/share/beakerlib/beakerlib.sh || exit 1

PACKAGE="kmod-devel"
IPIP="/usr/lib/modules/$(uname -r)/kernel/net/ipv4/ipip.ko.xz"

rlJournalStart
    rlPhaseStartSetup
        rlAssertRpm $PACKAGE
        rlAssertExists "$IPIP"
        rlRun "gcc -o test-libkmod test-libkmod.c -lkmod -lcmocka"
        rlRun "cp test-libkmod /usr/bin/"
    rlPhaseEnd

    rlPhaseStartTest
        rlLog "Starting libkmod tests ..."
        rlRun "/usr/bin/test-libkmod"
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "rm /usr/bin/test-libkmod"
        rlLog "libkmod tests done"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd

rlGetTestState
