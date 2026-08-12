#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /CoreOS/sudo/Sanity/sudoers-options-sanity-test
#   Description: This sanity test checks pre-defined (some are commented) options (examples) in sudoers file.
#   Author: Ales Marecek <amarecek@redhat.com>
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

rlJournalStart && {
  rlPhaseStartSetup && {
    [[ -z "$BEAKERLIB_LIBRARY_PATH" ]] && BEAKERLIB_LIBRARY_PATH="$(dirname "$(readlink -f "$0")")"
    rlRun "rlImport --all" 0 "Import libraries" || rlDie "cannot continue"
    tcfTry "Setup phase" && {
      tcfRun "rlCheckMakefileRequires"
      rlRun "TmpDir=\$(mktemp -d)" 0 "Creating tmp directory"
      CleanupRegister "rlRun 'rm -r $TmpDir' 0 'Removing tmp directory'"
      CleanupRegister 'rlRun "popd"'
      rlRun "pushd $TmpDir"
      CleanupRegister 'rlRun "rlFileRestore"'
      rlRun "rlFileBackup --clean /etc/sudoers"
      rpm -V sudo | grep /etc/sudoers && {
        # we need clean config file that is shipped with package
        rlRun "rm -rf /etc/sudoers"
        rlRun "yum -y reinstall sudo" 0 "Reinstalling '${PACKAGE}' package"
      }; :
      CleanupRegister 'rlRun "testUserCleanup"'
      rlRun "testUserSetup 2"
    tcfFin; }
  rlPhaseEnd; }

  tcfTry "Tests" --no-assert && {
    rlPhaseStartTest "Active options test - active sudo settings" && {
      if ( rlIsRHEL 6 && rlIsRHEL '>=6.8' ) || ( rlIsRHEL 7 && rlIsRHEL '>=7.3' ); then
        _OPTIONS=("!visiblepw" "always_set_home" "env_reset")
      elif rlIsRHEL; then
        _OPTIONS=("requiretty" "!visiblepw" "always_set_home" "env_reset")
      else
        if rlIsFedora 20; then
          _OPTIONS=("requiretty" "env_reset")
        else
          _OPTIONS=("!visiblepw" "env_reset")
        fi
      fi
      for _OPTION in ${_OPTIONS[@]}; do
        rlRun "grep '^Defaults\s\+${_OPTION}' /etc/sudoers" 0 "Test: '${_OPTION}' check"
      done
    rlPhaseEnd; }

    rlPhaseStartTest "Active options test - Evironment" && {
        for _OPTION in DISPLAY HOSTNAME USERNAME LC_COLLATE LC_MESSAGES LC_TIME LC_ALL XAUTHORITY; do
                rlRun "cat /etc/sudoers | grep '^Defaults\s\+env_keep' | grep '${_OPTION}'" 0 "Test: '${_OPTION}' check"
        done
        rlRun "grep '^Defaults\s\+secure_path\s\+=\s\+/sbin:/bin:/usr/sbin:/usr/bin' /etc/sudoers" 0 "Test: 'secure_path' check"
    rlPhaseEnd; }

    rlPhaseStartTest "Commented options test - examples" && {
        for _OPTION in "Host_Alias" "Cmnd_Alias" "User_Alias"; do
                rlRun "grep \"^#.*${_OPTION}.*\" /etc/sudoers" 0 "Test: '${_OPTION}' check"
        done
    rlPhaseEnd; }

    rlPhaseStartTest "pam_service and pam_login_service" && {
        CleanupRegister --mark 'rlRun "rlFileRestore --namespace pam_service"'
        rlRun "rlFileBackup --namespace pam_service --clean /etc/pam.d/ /etc/sudoers"
        rlRun "cat /etc/pam.d/sudo > /etc/pam.d/sudo2"
        rlRun "cat /etc/pam.d/sudo-i > /etc/pam.d/sudo2-i"
        rlRun "sed -i '/session.*pam_echo/d' /etc/pam.d/sudo"
        rlRun "sed -i '/session.*pam_echo/d' /etc/pam.d/sudo-i"
        rlRun "echo -e 'session\toptional\tpam_echo.so %%sudo pam_service' >> /etc/pam.d/sudo"
        rlRun "echo -e 'session\toptional\tpam_echo.so %%sudo-i pam_login_service' >> /etc/pam.d/sudo-i"
        rlRun "echo -e 'session\toptional\tpam_echo.so %%sudo2 pam_service' >> /etc/pam.d/sudo2"
        rlRun "echo -e 'session\toptional\tpam_echo.so %%sudo2-i pam_login_service' >> /etc/pam.d/sudo2-i"
        sudoers_file="$(cat /etc/sudoers)"
        rlRun -s "sudo id"
        rlAssertGrep '^%sudo pam_service' $rlRun_LOG
        rm -f $rlRun_LOG
        rlRun -s "sudo -i id"
        rlAssertGrep '^%sudo-i pam_login_service' $rlRun_LOG
        rm -f $rlRun_LOG
        tcfChk "change pam service name" && {
          echo "Defaults pam_service=sudo2" > /etc/sudoers
          echo "Defaults pam_login_service=sudo2-i" >> /etc/sudoers
          echo "$sudoers_file" >> /etc/sudoers
        tcfFin; }
        rlRun -s "sudo id"
        rlAssertGrep '^%sudo2 pam_service' $rlRun_LOG
        rm -f $rlRun_LOG
        rlRun -s "sudo -i id"
        rlAssertGrep '^%sudo2-i pam_login_service' $rlRun_LOG
        rm -f $rlRun_LOG
        CleanupDo --mark
    rlPhaseEnd; }

    rlPhaseStartTest "User and Group settings" && {
        rlRun "grep '^root\s\+ALL=(ALL)\s\+ALL' /etc/sudoers" 0 "Test: 'root' user check"
        # specific "%wheel" command in RHEL-7 - allowing "wheel" group for super-trooper admin-needs by Anaconda
        rlIsRHEL 4 5 6
        [ $? -eq 0 ] && rlRun "grep '^#.*%wheel\s\+ALL=(ALL)\s\+ALL' /etc/sudoers" 0 "Test: 'wheel' (commented) group check" || rlRun "grep '^%wheel\s\+ALL=(ALL)\s\+ALL' /etc/sudoers" 0 "Test: 'wheel' group check"
        rlRun "grep '^#.*%sys' /etc/sudoers" 0 "Test: 'sys' (commented) group check"
    rlPhaseEnd; }
    
    ! rlIsRHEL '<6' && rlPhaseStartTest 'env_check' && {
      tcfChk "env_check" && {
        tcfChk "setup phase" && {
          rlRun "cat /etc/sudoers > sudoers"
          CleanupRegister "
            rlRun 'cat sudoers > /etc/sudoers'
            rlRun \"export TZ='${TZ}'\"
          "
          clean_sudoers=$CleanupRegisterID
          rlRun "echo 'Defaults env_check += \"TZ\"' >> /etc/sudoers"
          rlRun "echo 'Defaults env_keep += \"TZ\"' >> /etc/sudoers"
          rlRun "echo 'Defaults !authenticate' >> /etc/sudoers"
          rlRun "sed -ri 's/(Defaults\s+)(requiretty)/\1!\2/' /etc/sudoers"
          rlRun "cat -n /etc/sudoers | tr '\t' ' ' | grep -Pv '^ +[0-9]+ +(#|$)'"
        tcfFin; }
        tcfTry "test" && {
          tcfChk "test allowed values" && {
            for TZ in AB America/New_York /usr/share/zoneinfo/America/New_York; do
              rlRun "export TZ='$TZ'"
              rlRun -s "env"
              rlAssertGrep "^TZ=$TZ" $rlRun_LOG
              rm -f $rlRun_LOG
              rlRun -s "sudo env"
              rlAssertGrep "^TZ=$TZ" $rlRun_LOG
              rm -f $rlRun_LOG
            done
          tcfFin; }
          tcfChk "test wrong values" && {
            for TZ in "A B" \
                      /etc/hosts \
                      /usr/share/zoneinfo/../zoneinfo/America/New_York \
                      1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890 \
                      ; do
              rlRun "export TZ='$TZ'"
              rlRun -s "env"
              rlAssertGrep "^TZ=$TZ" $rlRun_LOG
              rm -f $rlRun_LOG
              rlRun -s "sudo env"
              rlAssertNotGrep "^TZ=$TZ" $rlRun_LOG
              rm -f $rlRun_LOG
            done
          tcfFin; }
        tcfFin; }
        tcfChk "cleanup phase" && {
          CleanupDo $clean_sudoers
        tcfFin; }
      tcfFin; }
    rlPhaseEnd; }

    rlPhaseStartTest "test, requiretty" && {
      tcfChk && {
        tcfChk "setup" && {
          CleanupRegister --mark 'rlRun "rlFileRestore --namespace requiretty"'
          rlRun "rlFileBackup --clean --namespace requiretty /etc/sudoers"
          rlRun "echo '$testUser ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers"
        tcfFin; }

        tcfTry && {
          tcfChk "test, requiretty" && {
            rlRun "sed -i '/requiretty/d' /etc/sudoers"
            rlRun "echo 'Defaults    requiretty' >> /etc/sudoers"
            rlRun -s "nohup su -l -c 'sudo id' $testUser > /dev/stdout" 1
            rlAssertGrep 'you must have a tty' $rlRun_LOG
            rm -f $rlRun_LOG
          tcfFin; }

          tcfChk "test, !requiretty" && {
            rlRun "sed -i '/requiretty/d' /etc/sudoers"
            rlRun "echo 'Defaults    !requiretty' >> /etc/sudoers"
            rlRun "nohup su -l -c 'sudo id' $testUser > /dev/stdout"
          tcfFin; }
        tcfFin; }

        tcfChk "cleanup" && {
          CleanupDo --mark
        tcfFin; }
      tcfFin; }
    rlPhaseEnd; }
    
   if ! rlIsRHEL '<7.4'; then
    rlPhaseStartTest "test, iolog" && {
      tcfChk && {
        iolog_config() {
          rlLog "create config"
          cat > /etc/sudoers.d/iolog <<EOF
Defaults     !requiretty, iolog_dir=/var/log/sudo-io/%{user}
$1
$testUser    ALL = (ALL) NOPASSWD: LOG_INPUT: LOG_OUTPUT: ALL
EOF
        }
        tcfChk "setup" && {
          CleanupRegister --mark 'rlRun "rlFileRestore --namespace iolog"'
          rlRun "rlFileBackup --clean --namespace iolog /etc/sudoers.d/iolog"
          rlRun "rm -rf /var/log/sudo-io"
        tcfFin; }

        tcfTry "test" && {
          tcfChk "test, basic test" && {
            rlRun "rm -rf /var/log/sudo-io"
            iolog_config
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            rlRun -s "ls -laR /var/log/sudo-io"
            rlAssertGrep "drwx------.+root root\s+.*$testUser" $rlRun_LOG -Eq
            rm -f $rlRun_LOG
          tcfFin; }

          tcfChk "test, user test" && {
            rlRun "rm -rf /var/log/sudo-io"
            iolog_config "Defaults iolog_user=$testUser"
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            rlRun -s "ls -laR /var/log/sudo-io"
            rlAssertGrep "drwx------.+$testUser $testUserGroup\s+.*$testUser" $rlRun_LOG -Eq
            rm -f $rlRun_LOG
          tcfFin; }

          tcfChk "test, group test" && {
            rlRun "rm -rf /var/log/sudo-io"
            iolog_config "Defaults iolog_group=$testUserGroup"
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            rlRun -s "ls -laR /var/log/sudo-io"
            rlAssertGrep "drwx------.+root $testUserGroup\s+.*$testUser" $rlRun_LOG -Eq
            rm -f $rlRun_LOG
          tcfFin; }

          tcfChk "test, mode test" && {
            rlRun "rm -rf /var/log/sudo-io"
            iolog_config "Defaults iolog_mode=770"
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            rlRun -s "ls -laR /var/log/sudo-io"
            rlAssertGrep "drwxrwx---.+root root\s+.*$testUser" $rlRun_LOG -Eq
            rm -f $rlRun_LOG
          tcfFin; }
        tcfFin; }

        tcfChk "cleanup" && {
          CleanupDo --mark
        tcfFin; }
      tcfFin; }
    rlPhaseEnd; }

    rlPhaseStartTest "test, MAIL, NOMAIL" && {
      tcfChk && {
        create_config() {
          rlLog "create config"
          cat > /etc/sudoers.d/test <<EOF
Defaults !requiretty, mailto=emailto@domain.com
${1:+"Defaults $1"}
$testUser    ALL = (ALL) NOPASSWD: $2 ALL
${testUser[1]}    ALL = (ALL) NOPASSWD: ALL
EOF
        }
        clean_mail_queue() {
          which postsuper >& /dev/null && {
            postsuper -d ALL
          }
          [[ -e /var/spool/mqueue/ ]] && [[ -n "$(ls -1 /var/spool/mqueue/)" ]] && {
            rm -rf /var/spool/mqueue/*
          }
          [[ -e /var/spool/clientmqueue/ ]] && [[ -n "$(ls -1 /var/spool/clientmqueue/)" ]] && {
            rm -rf /var/spool/clientmqueue/*
          }
          [[ -e /var/spool/postfix/maildrop/ ]] && [[ -n "$(ls -1 /var/spool/postfix/maildrop/)" ]] && {
            rm -rf /var/spool/postfix/maildrop/*
          }
        }
        get_last_mail_log() {
          sleep 1
          tail -n +$(($last_line_num + 1)) /var/log/maillog | grep -iv 'connection timed out' > last_mail.log
          mailq >> last_mail.log
          mailq -Ac >> last_mail.log
          rlRun "cat last_mail.log" 0-255
          clean_mail_queue
          last_line_num=`cat /var/log/maillog | wc -l`
        }
        tcfChk "setup" && {
          CleanupRegister --mark 'rlRun "rlFileRestore --namespace MAIL"'
          rlRun "rlFileBackup --clean --namespace MAIL /etc/sudoers.d/test"
          clean_mail_queue
          get_last_mail_log
        tcfFin; }

        tcfTry "test" && {

          tcfChk "test, mail_always test" && {
            create_config mail_always
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            get_last_mail_log
            rlAssertGrep 'emailto@domain.com' last_mail.log -iq
          tcfFin; }

          tcfChk "test, NOMAIL test" && {
            create_config mail_always NOMAIL:
            last_line_num=`cat /var/log/maillog | wc -l`
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            get_last_mail_log
            rlAssertNotGrep 'emailto@domain.com' last_mail.log -iq
            rlRun "su -c 'sudo /bin/ls /' - ${testUser[1]}" 0
            get_last_mail_log
            rlAssertGrep 'emailto@domain.com' last_mail.log -iq
          tcfFin; }

          tcfChk "test, MAIL test" && {
            create_config '' MAIL:
            last_line_num=`cat /var/log/maillog | wc -l`
            rlRun "su -c 'sudo /bin/ls /' - $testUser" 0
            get_last_mail_log
            rlAssertGrep 'emailto@domain.com' last_mail.log -iq
            rlRun "su -c 'sudo /bin/ls /' - ${testUser[1]}" 0
            get_last_mail_log
            rlAssertNotGrep 'emailto@domain.com' last_mail.log -iq
          tcfFin; }

        tcfFin; }

        tcfChk "cleanup" && {
          CleanupDo --mark
        tcfFin; }
      tcfFin; }
    rlPhaseEnd; }

    rlPhaseStartTest "test mute unknown defaults" && {
      CleanupRegister --mark 'rlRun "rlFileRestore --namespace mute_unknown"'
      rlRun "rlFileBackup --clean --namespace mute_unknown /etc/sudoers.d/test"
      cat > /etc/sudoers.d/test <<EOF
Defaults     blahblah
$testUser    ALL = (ALL) NOPASSWD: ALL
EOF
      rlRun -s "su -c 'sudo id' - $testUser" 0
      rlAssertGrep 'uid=0(root)' $rlRun_LOG
      rlAssertGrep 'unknown defaults entry.*blahblah' $rlRun_LOG
      rm -f $rlRun_LOG
      cat > /etc/sudoers.d/test <<EOF
Defaults     blahblah
Defaults     ignore_unknown_defaults
$testUser    ALL = (ALL) NOPASSWD: ALL
EOF
      rlRun -s "su -c 'sudo id' - $testUser" 0
      rlAssertGrep 'uid=0(root)' $rlRun_LOG
      rlAssertNotGrep 'unknown' $rlRun_LOG
      rm -f $rlRun_LOG
      CleanupDo --mark
    rlPhaseEnd; }
  fi
  tcfFin; }

  rlPhaseStartCleanup && {
    tcfChk "Cleanup phase" && {
      CleanupDo
    tcfFin; }
    tcfCheckFinal
  rlPhaseEnd; }

  rlJournalPrintText
rlJournalEnd; }
