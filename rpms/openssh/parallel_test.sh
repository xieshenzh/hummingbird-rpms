#!/usr/bin/bash
set -uexo pipefail

# The custom %check script to run the OpenSSH upstream testsuite in parallel.
#
# The upstream testsuite is serial,
# so the idea here is to split LTESTS into several t-exec-$ii parts
# and run them in parallel, using make, each in its own build subtree.
# The remaining test groups (file-tests, interop-tests, extra-tests, unit)
# run sequentially in the main build tree after the parallel phase.

PARALLEL_MAKEFILE=$1

SPLIT=24

# work around a selinux restriction:
chcon -t unconfined_exec_t ssh-sk-helper || :

# work around something else that only crops up in brew
export TEST_SSH_UNSAFE_PERMISSIONS=1

# create directories to store our files in:
mkdir -p .t .ltests/{in,not-in}

# patch testsuite: use different ports to avoid port collisions
grep -REi 'port=[2-9][0-9]*' regress
sed -i 's|PORT=4242|PORT=$(expr $TEST_SSH_PORT + 1)|' \
    regress/test-exec.sh*
sed -i 's|^P=3301  # test port|P=$(expr $TEST_SSH_PORT + 1)|' \
    regress/multiplex.sh*
sed -i 's|^fwdport=3301|fwdport=$(expr $TEST_SSH_PORT + 1)|' \
    regress/cfgmatch.sh* regress/cfgmatchlisten.sh*
sed -i 's|^LFWD_PORT=.*|LFWD_PORT=$(expr $TEST_SSH_PORT + 1)|' \
    regress/forward-control.sh*
sed -i 's|^RFWD_PORT=.*|RFWD_PORT=$(expr $TEST_SSH_PORT + 2)|' \
    regress/forward-control.sh*
# verify no hardcoded ports remain (except kdc_port which is only used
# by extra-tests running sequentially, so collisions aren't a concern)
( ! grep -REi 'port=[2-9][0-9]*' regress --include='*.sh' \
    | grep -v kdc_port)

# patch testsuite: use short paths for Unix domain control sockets
# to avoid exceeding the 108-byte sun_path limit (OpenSSH appends a
# temporary suffix during socket creation, adding ~17 bytes)
for f in regress/forward-control.sh regress/connection-timeout.sh \
         regress/ssh-tty.sh; do
    sed -i 's|^CTL=$OBJ/ctl-sock|make_tmpdir; CTL=${SSH_REGRESS_TMP}/ctl-sock|' "$f"
done
sed -i 's|^MUXPATH=\$OBJ/mux\.\$\$|make_tmpdir; MUXPATH=${SSH_REGRESS_TMP}/mux.$$|' \
    regress/channel-timeout.sh

# extract LTESTS list to .ltests/all:
grep -Ex 'tests:[[:space:]]*(prep )?file-tests t-exec( interop-tests)?( extra-tests)? unit' Makefile
echo -ne '\necho-ltests:\n\techo ${LTESTS}' >> regress/Makefile
make -s -C regress echo-ltests | tr ' ' '\n' > .ltests/all

# separate ltests into $SPLIT roughly equal .ltests/in/$ii parts:
grep -qFx connect .ltests/all
( ! grep -qFx nonex .ltests/all )
split -d -a2 --number=l/$SPLIT .ltests/all .ltests/in/
wc -l .ltests/in/*
grep -qFx connect .ltests/in/*

# generate the inverses of them --- .ltests/not-in/$ii:
( ! grep -qFx nonex .ltests/in/* )
for ((i = 0; i < SPLIT; i++)); do ii=$(printf %02d $i);
    while read -r tname; do
        if ! grep -qFx "$tname" ".ltests/in/$ii"; then
            echo -n "$tname " >> ".ltests/not-in/$ii"
        fi
    done < .ltests/all
done
grep . .ltests/not-in/*
grep -qFx connect .ltests/in/00
for ((i = 1; i < SPLIT; i++)); do ii=$(printf %02d $i);
    ( ! grep -qFx connect .ltests/in/$ii )
done

# prepare test directories (only for the parallel LTESTS partitions):
PARTS=''
for ((i = 0; i < SPLIT; i++)); do ii=$(printf %02d $i);
    PARTS+="t-exec-$ii "
    mkdir .t/t-exec-$ii
    cp -ra * .t/t-exec-$ii/
    sed -i "s|abs_top_srcdir=.*|abs_top_srcdir=$(pwd)/.t/t-exec-$ii|" \
        .t/t-exec-$ii/Makefile
    sed -i "s|abs_top_builddir=.*|abs_top_builddir=$(pwd)/.t/t-exec-$ii|" \
        .t/t-exec-$ii/Makefile
    sed -i "s|^BUILDDIR=.*|BUILDDIR=$(pwd)/.t/t-exec-$ii|" \
        .t/t-exec-$ii/Makefile
done

# run LTESTS partitions in parallel:
time make -f "$PARALLEL_MAKEFILE" -j$(nproc) $PARTS

# run the remaining test groups sequentially in the main build tree:
export TEST_SSH_PORT=4200
make file-tests
make interop-tests
make extra-tests
make unit
