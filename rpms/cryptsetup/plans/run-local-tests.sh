#!/bin/bash

set -eux
set -o pipefail

pushd $TMT_SOURCE_DIR/cryptsetup-*/tests
make -f Makefile.localtest tests
popd
