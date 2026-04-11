#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /tools/boost/Sanity/boost-testsuite-sanity
#   Description: boost testing by upstream testsuite
#   Author: Michal Kolar <mkolar@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2021 Red Hat, Inc.
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
. /usr/share/beakerlib/beakerlib.sh || exit 1

BUILD_USER=${BUILD_USER:-bstbld}
TESTS_COUNT_MIN=${TESTS_COUNT_MIN:-100}
PACKAGE="boost"
REQUIRES="$PACKAGE rpm-build boost-b2"
if rlIsFedora; then
  REQUIRES="$REQUIRES dnf-utils"
else
  REQUIRES="$REQUIRES yum-utils"
fi

rlJournalStart
  rlPhaseStartSetup
    rlShowRunningKernel
    rlAssertRpm --all
    rlRun "TmpDir=`mktemp -d /home/boost.XXXXXXXXXX`"  # work in /home due to high demands on disk space
    rlRun "cp tests $TmpDir"
    rlRun "pushd $TmpDir"
    rlFetchSrcForInstalled $PACKAGE
    rlRun "useradd -M -N $BUILD_USER" 0,9
    [ "$?" == "0" ] && rlRun "del=yes"
    rlRun "chown -R $BUILD_USER:users $TmpDir"
  rlPhaseEnd

  rlPhaseStartSetup "build boost"
    rlRun "rpm -D \"_topdir $TmpDir\" -U *.src.rpm"
    rlRun "dnf builddep -y $TmpDir/SPECS/*.spec"
    rlRun "sed -i -e 's/^%prep/%prep\n%dump/' $TmpDir/SPECS/*.spec"
    rlRun "su -c 'rpmbuild -D \"_topdir $TmpDir\" -bp $TmpDir/SPECS/*.spec &>$TmpDir/rpmbuild.log' $BUILD_USER"
    rlRun "rlFileSubmit $TmpDir/rpmbuild.log"
    rlRun "toplev_dirname=`awk '/toplev_dirname/{print $3; exit}' $TmpDir/rpmbuild.log`"
    cd $TmpDir/BUILD/$toplev_dirname
    if [ $? -ne 0 ]; then
        # handle rpm 4.20 build directory difference
        # https://github.com/rpm-software-management/rpm/issues/3147
       rlRun "cd $TmpDir/BUILD/*-build/$toplev_dirname"
    fi
    # now we know the top-level build dir, keep it for later
    rlRun "BuildDir=$(pwd)"
    rlRun "su -c './bootstrap.sh &>$TmpDir/bootstrap.log' $BUILD_USER"
    rlRun "rlFileSubmit $TmpDir/bootstrap.log"
  rlPhaseEnd

  rlPhaseStartTest "run testsuite"
    while read test_path; do
      if [ -f $BuildDir/libs/$test_path/test/Jamfile* ]; then
        rlRun "cd $BuildDir/libs/$test_path/test"
        if [ "$test_path" = regex ]; then
          rlRun "[ -e ../include ] || ln -s ../../ ../include"
        fi
        rlRun "su -c '/usr/bin/b2 -d1 --build-dir=$TmpDir/test-build &>>$TmpDir/testsuite.log' $BUILD_USER"
        rm -fr $TmpDir/test-build
      else
        rlLogInfo "$test_path/Jamfile* not found, skipping"
      fi
    done <$TmpDir/tests
    rlRun "rlFileSubmit $TmpDir/testsuite.log"
  rlPhaseEnd

  rlPhaseStartTest "evaluate results"
    rlRun "cd $TmpDir"
    rlRun "grep -E '\.\.\.failed .+$TmpDir/test-build' testsuite.log" 1 "There should be no failure"
    rlRun "tests_count=\$(grep -E '\*\*passed\*\*.+$TmpDir/test-build' testsuite.log | wc -l)"
    [ "$tests_count" -ge "$TESTS_COUNT_MIN" ] && rlLogInfo "Test counter: $tests_count" || rlFail "Test counter $tests_count should be greater than or equal to $TESTS_COUNT_MIN"
  rlPhaseEnd

  rlPhaseStartCleanup
    rlRun "popd"
    rlRun "rm -r $TmpDir"
    [ "$del" == "yes" ] && rlRun "userdel $BUILD_USER"
  rlPhaseEnd
rlJournalPrintText
rlJournalEnd
