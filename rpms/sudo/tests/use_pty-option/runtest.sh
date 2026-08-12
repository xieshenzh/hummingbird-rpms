#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/sudo/Sanity/use_pty-option
#   Description: checks if use_pty option in /etc/sudoers works as expected
#   Author: Milos Malik <mmalik@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2011 Red Hat, Inc. All rights reserved.
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

# Include rhts environment
. /usr/bin/rhts-environment.sh
. /usr/share/beakerlib/beakerlib.sh

PACKAGE="sudo"
USER_NAME="user${RANDOM}"
USER_SECRET="s3kr3T${RANDOM}"

rlJournalStart
    rlPhaseStartSetup
        rlAssertRpm ${PACKAGE}
        OUTPUT_FILE=`mktemp`
        rlFileBackup /etc/sudoers
        rlFileBackup --clean ~/.ssh

        rlRun "useradd ${USER_NAME}"
        rlRun "echo ${USER_SECRET} | passwd --stdin ${USER_NAME}"
        rlRun "cp ./forker.sh /home/${USER_NAME}/"
        rlRun "chown ${USER_NAME}:${USER_NAME} /home/${USER_NAME}/forker.sh"
        rlRun "chmod u+x /home/${USER_NAME}/forker.sh"
        rlRun "echo \"${USER_NAME} ALL = NOPASSWD: /home/${USER_NAME}/forker.sh\" >> /etc/sudoers"
        rlRun "sed -i 's/^.*requiretty.*$//' /etc/sudoers"
        rlRun "echo \"Defaults !requiretty\" >> /etc/sudoers"
        rlRun "> ~/.ssh/known_hosts"
    rlPhaseEnd

    rlPhaseStartTest "use_pty option is enabled"
        rlRun "sed -i 's/^.*use_pty.*$//' /etc/sudoers"
        rlRun "echo \"Defaults use_pty\" >> /etc/sudoers"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost ./forker.sh 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertNotGrep "ping statistics" ${OUTPUT_FILE}
    rlPhaseEnd

    rlPhaseStartTest "use_pty option is disabled"
        rlRun "sed -i 's/^.*use_pty.*$//' /etc/sudoers"
        rlRun "echo \"Defaults !use_pty\" >> /etc/sudoers"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost ./forker.sh 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertGrep "ping statistics" ${OUTPUT_FILE}
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "userdel -rf ${USER_NAME}"
        rlFileRestore
        rm -f ${OUTPUT_FILE}
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd

