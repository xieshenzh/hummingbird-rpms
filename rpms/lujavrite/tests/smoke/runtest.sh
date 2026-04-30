#!/bin/bash
# Author: Mikolaj Izdebski <mizdebsk@redhat.com>
. /usr/share/beakerlib/beakerlib.sh

rlJournalStart

  rlPhaseStartSetup
    rlAssertRpm lujavrite
    rlAssertRpm java-25-openjdk-headless
    export JAVA_HOME=/usr/lib/jvm/jre-25-openjdk
  rlPhaseEnd

  rlPhaseStartTest
    rlAssertExists "${JAVA_HOME}"
    rlAssertExists "${JAVA_HOME}/lib/server/libjvm.so"
    rlRun -s "lua smoke.lua"
    rlAssertGrep "JVM initially initialized? false" $rlRun_LOG
    rlAssertGrep "JVM initialized now? true" $rlRun_LOG
    rlAssertGrep "Java version is 25" $rlRun_LOG
    rlAssertGrep "foo is bar" $rlRun_LOG
    rlAssertGrep "nil in Lua is null in Java" $rlRun_LOG
  rlPhaseEnd

rlJournalEnd
rlJournalPrintText
