#!/bin/bash
# vim: dict+=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of kmod/sanity
#   Description: Checking the basic function of kmod
#   Author: Shaohui Deng <shdeng@redhat.com>
#   Author: Chunyu Hu <chuhu@redhat.com>
#   Update: Ziqian SUN <zsun@redhat.com>
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
. /usr/share/beakerlib/beakerlib.sh || exit 1
. lib.sh

PACKAGE="kmod"
if [ -z $TESTARGS ]; then
export TESTARGS=$(rpm -q --queryformat '%{version}-%{release}\n' -qf /boot/config-$(uname -r))
fi
name=kernel
version=$(echo $TESTARGS | awk 'BEGIN {FS="-"} {print $1 }')
release=$(echo $TESTARGS | awk 'BEGIN {FS="-"} {print $2 }')
DEBUG=0
result=FAIL
FAIL=0
PASS=0
arch=`uname -m`

export TESTS=${TESTS:-kmod_load_insmod kmod_load_modprobe kmod_modinfo}

# Functions
function kmod_load_insmod()
{
    local m
    for m in ${existed_unloaded}; do
        #local m_name=$(basename ${m//.ko}) won't work for compressed kmod
        local m_name=$(basename ${m} | sed "s/.ko//;s/.xz//;s/.gz//")
        rlRun "insmod $mod_path/$m"
        rlRun "lsmod | grep ${m_name}"
        rlRun "rmmod ${m_name}"
        rlRun "lsmod | grep ${m_name}" 1-255
    done
}

function kmod_load_modprobe()
{
    rlRun "modprobe tun"
    rlRun "modprobe -r tun"
}

function kmod_modinfo()
{
    for m in ${existed_unloaded}; do
        local m_name=$(basename ${m} | sed "s/.ko//;s/.xz//;s/.gz//")
        rlRun "modinfo -l $m_name"
    done
}

RunKmod() {
    local tests
    for tests in $@; do
        rlPhaseStartTest $tests
        case $tests in
            load_unload_kernel_modules)
            load_unload_kernl_modules_func
            ;;
            load_unload_compressed_kernel_modules)
            load_unload_compressed_kernel_module_func
            ;;
            *)
            $tests
            ;;
        esac
        rlPhaseEnd
    done
}

rlJournalStart
    rlPhaseStartSetup
        mod_list="/kernel/net/ipv4/udp_tunnel.ko /kernel/net/wireless/lib80211.ko /kernel/net/wireless/ath/ath9k/ath9k.ko /kernel/net/ipv4/udp_tunnel.ko.xz /kernel/net/wireless/lib80211.ko.xz /kernel/net/wireless/ath/ath9k/ath9k.ko.xz"
        if [[ -L /lib && -d /lib ]]; then
            mod_path=/usr/lib/modules/`uname -r`/
        else
            mod_path=/lib/modules/`uname -r`/
        fi
        existed_unloaded=""
        local m
        for m in ${mod_list}; do
            test -f $mod_path/$m && existed_unloaded+="$m "
        done
        [ -z "$existed_unloaded" ] && rlDie "There is no right module path to use : $mod_list"
    rlPhaseEnd

    rlPhaseStartTest
        RunKmod $TESTS
    rlPhaseEnd

    if ! (lsmod | grep gre)
    then
        wparam="gre"
    elif ! (lsmod | grep uwb)
    then
        wparam="uwb"
    else
        wparam="atm"
    fi
    rlPhaseStartTest "Modprobe with wrong parameter"
        rlRun -l "modprobe $wparam BADPARAM=this_should_fail" 0-1
        rlRun -l "dmesg | grep -i 'Unknown parameter' | grep 'BADPARAM' | grep '$wparam'"
    rlPhaseEnd

    rlPhaseStartCleanup
    rlPhaseEnd

    rlJournalPrintText
rlJournalEnd
