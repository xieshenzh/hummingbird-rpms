#!/bin/bash

dir_extra=/usr/lib/modules/$(uname -r)/extra
dir_updates=/usr/lib/modules/$(uname -r)/updates
dir_weak_updates=/usr/lib/modules/$(uname -r)/weak-updates
kmods=(base kmod_test_force kmod_test)

function kmod_log_warn()
{
	echo "WARN: $*"
}


function kmod_log_pass()
{
	echo "PASS: $*"
}

function kmod_log_fail()
{
	echo "FAIL: $*"
}

function kmod_log_info()
{
	echo "INFO: $*"
}

function kmod_log_err()
{
	echo "ERR: $*"
}

function kmod_verify_exist()
{
	local files=$*
	local ret=0
	local msg=0
	local f=
	for f in $files; do
		if ! test -f $f; then
			ret=$((ret + 1))
			continue
		fi
	done
	return $ret
}

function kmod_verify_loaded()
{
	local kmods=$*
	local ret=$?
	for kmod in $kmods; do
		lsmod | grep -w ${kmod/.ko/}
		if [ $? -ne 0 ]; then
			ret=$((ret+1))
			continue
		fi
	done
	return $ret
}

# tainted should be shown
function kmod_verify_dmesg()
{
	local str="$1"
	dmesg | grep -i "$str"
	return $?
}

function kmod_verify_tainted()
{
	local kmod=$1
	shift
	local taint_expect=$*
	local ret=0
	local checker=./parse_taint.sh
	local tainted=$(cat /proc/sys/kernel/tainted)
	kmod_log_info "Kernel taint value: $tainted"
	local flag=
	for flag in $taint_expect; do
		$checker $tainted | grep -we $flag || ret=$((ret + 1))
	done
	return $ret
}

function kmod_verify_tainted_module()
{
	local kmod=$1
	shift
	local taint_expect="$*"
	local ret=0

	if ! test -f /sys/module/$kmod/taint; then
		kmod_log_info "Kmod taint value on module $kmod not supported"
		kmod_log_info "$(ls /sys/module/$kmod/)"
		return 0
	fi

	local kmod_tainted=$(cat /sys/module/$kmod/taint)
	ret=$(( $ret + 1 ))
	kmod_log_info "Kmod taint value: $kmod_tainted"

	local flag=
	local grep_exec="grep"
	for flag in $*; do
		grep_exec+=" -e $flag"
	done

	echo "$kmod_tainted" | $grep_exec
	ret=$?
	return $ret
}

function kmod_check_result()
{
	local opt=$1
	shift 1
	local dir=$1
	shift 1
	local files=$*
	case $opt in
		compile)
		for f in $files; do
			if kmod_verify_exist $dir/$f; then
				kmod_log_pass "File exist: $dir/$f"
			else
				kmod_log_fail "File does not exist: $dir/$f"
				return 1
			fi
		done
		;;
		load)
		for f in $files; do
			if kmod_verify_loaded $f; then
				kmod_log_fail $f not loaded
				return 1
			else
				kmod_log_pass $f loaded
			fi
		done
		;;
		unload)
		for f in $files; do
			if ! kmod_verify_loaded $f; then
				kmod_log_fail $f unloaded
				return 1
			else
				kmod_log_pass $f not loaded
			fi
		done
		;;
		*)
		;;
	esac
	return 0
}

# Locate the compiled kmods into several dirs for different
# tests.
function kmod_locate_extra()
{
	local ret=0
	if ! test -d $dir_extra; then
		kmod_log_warn "$dir_extra does not exist."
		return 1
	fi
	local f=
	for f in $*; do
		cp -f $f $dir_extra || ret=$((ret + 1))
	done
	depmod || ret=$((ret+1))
	return $ret
}

function kmod_locate_updates()
{
	local ret=0
	if ! test -d $dir_updates; then
		kmod_log_warn "$dir_updates does not exist."
		return 1
	fi
	for f in $*; do
		cp -f $f $dir_updates || ret=$((ret + 1))
	done
	depmod || ret=$((ret+1))
	return $ret
}

function kmod_locate_weak_updates()
{
	local ret=0
	if ! test -d $dir_weak_updates; then
		kmod_log_warn "$dir_weak_updates does not exist."
		return 1
	fi
	for f in $*; do
		cp -f $f $dir_weak_updates || ret=$((ret + 1))
	done
	depmod || ret=$((ret+1))
	return $ret
}

function kmod_cleanup_all()
{
	local ret=$?
	for ko in ${kmod[*]}; do
		test -f $dir_extra/${ko}.ko && rm -r $dir_extra/${ko}.ko
		test -f $dir_weak_updates/${ko}.ko && rm -r $dir_weak_updates/${ko}.ko
		test -f $dir_updates/${ko}.ko && rm -r $dir_extra/${ko}.ko
	done
}

# The kmods should be loaded now.
function kmod_check_all_loaded()
{
	local ret=$?
	lsmod | grep base -w
	ret=$(($ret + $?))
	lsmod | grep -w kmod_test_dependency
	ret=$(($ret + $?))
	lsmod | grep -w kmod_test_force
	ret=$(($ret + $?))
	return $ret
}

function kmod_unload_all()
{
	local ret=0
	lsmod | grep -w kmod_test_force && rmmod kmod_test_force
	lsmod | grep -w base && rmmod base
	! lsmod | grep -w base
	ret=$(($ret | $?))
	! lsmod | grep -w kmod_test_force
	ret=$(($ret | $?))
	return $ret
}


function kmod_load_all()
{
	local kmod=
	kmod_unload_all || return $?
	for kmod in $*; do
		modprobe $kmod || return $?
	done
}

function kmod_load_all_force()
{
	local kmod=
	kmod_unload_all || return $?
	for kmod in $*; do
		modprobe -f $kmod || return $?
		dmesg | tail -n 10
	done
}

