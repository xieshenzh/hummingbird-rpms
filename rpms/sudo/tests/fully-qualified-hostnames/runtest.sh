#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/sudo/Sanity/fully-qualified-hostnames
#   Description: checks if sudo works correctly when FQDN is used in /etc/sudoers
#   Author: Milos Malik <mmalik@redhat.com>
#   Edit: Ales "alich" Marecek <amarecek@redhat.com>
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
CONFIG_FILE="/etc/sudoers"
OUTPUT_FILE="sudo.log"

rlJournalStart
    rlPhaseStartSetup
        rlAssertRpm ${PACKAGE}
        rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
        rlRun "cp ssh-sudo.exp ${TmpDir}" 0 "Copying expect file"
        rlRun "pushd $TmpDir"
        OUTPUT_FILE="${TmpDir}/${OUTPUT_FILE}"
        rlFileBackup ${CONFIG_FILE} ~/.ssh
        id ${USER_NAME} && userdel -r ${USER_NAME}
        rlRun "useradd ${USER_NAME}"
        rlRun "echo ${USER_SECRET} | passwd --stdin ${USER_NAME}"
        rlRun "sed -i 's/^.*requiretty.*$//' ${CONFIG_FILE}"
        rlRun "sed -i 's/^.*lecture.*$//' ${CONFIG_FILE}"
        rlRun "echo \"Defaults !requiretty, !lecture\" >> ${CONFIG_FILE}"
        rlRun "echo \"${USER_NAME} ${HOSTNAME} = (root) `which id`\" >> ${CONFIG_FILE}"
        rlRun "> ~/.ssh/known_hosts"
    rlPhaseEnd

    if rlIsRHEL 5; then
    rlPhaseStartTest
        rlRun "strings `which sudo` | grep fqdn"
    rlPhaseEnd
    fi

    if echo ${HOSTNAME} | grep -q '^localhost'; then
    rlPhaseStartTest
        rlLogInfo "skipping fqdn option enabled tests, cannot run with local-only host name ${HOSTNAME}"
    rlPhaseEnd
    else
    rlPhaseStartTest "fqdn option is enabled, command is valid"
        rlRun "sed -i 's/^.*fqdn.*$//' ${CONFIG_FILE}"
        rlRun "echo \"Defaults fqdn\" >> ${CONFIG_FILE}"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost id 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertGrep "uid=0.*gid=0.*groups=0" ${OUTPUT_FILE}
    rlPhaseEnd

    rlPhaseStartTest "fqdn option is enabled, command is invalid"
        rlRun "sed -i 's/^.*fqdn.*$//' ${CONFIG_FILE}"
        rlRun "echo \"Defaults fqdn\" >> ${CONFIG_FILE}"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost w 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertGrep "user.*is not allowed to execute" ${OUTPUT_FILE}
    rlPhaseEnd
    fi

    rlPhaseStartTest "fqdn option is disabled, command is valid"
        rlRun "sed -i 's/^.*fqdn.*$//' ${CONFIG_FILE}"
        rlRun "echo \"Defaults !fqdn\" >> ${CONFIG_FILE}"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost id 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertGrep "uid=0.*gid=0.*groups=0" ${OUTPUT_FILE}
    rlPhaseEnd

    rlPhaseStartTest "fqdn option is disabled, command is invalid"
        rlRun "sed -i 's/^.*fqdn.*$//' ${CONFIG_FILE}"
        rlRun "echo \"Defaults !fqdn\" >> ${CONFIG_FILE}"
        rlRun "./ssh-sudo.exp ${USER_NAME} ${USER_SECRET} localhost w 2>&1 | tee ${OUTPUT_FILE}"
        rlAssertGrep "user.*is not allowed to execute" ${OUTPUT_FILE}
    rlPhaseEnd

    rlPhaseStartCleanup
        rlRun "userdel -rf ${USER_NAME}"
        rlFileRestore
        rlRun "popd"
        rlRun "rm -r $TmpDir" 0 "Removing tmp directory"
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd

