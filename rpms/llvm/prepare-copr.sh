#!/bin/bash -e

# This script prepares a remote copr instance. It ensures everything is
# installed (vim, tmux, make) on the copr instance and directories exist in
# specific locations and the copr instance time is prolonged by 24 hours.
#
# Usage: `make prepare-copr IP=<COPR_IP>` to prepare a machine.

set +e
echo "REMOTE-INFO: Prolong the copr instance"
copr-builder prolong --hours 24
set -e

echo "REMOTE-INFO: Install tmux vim and make"
dnf install -qy tmux vim make

echo "REMOTE-INFO: Make vim default editor (possibly removing nano as default editor)"
dnf install -y vim-default-editor --allowerasing

echo "REMOTE-INFO: Get mock uniqueext from copr build"
mock_uniqueext=$(grep -oP 'uniqueext\s*(\K[^\s]+)' /var/lib/copr-rpmbuild/main.log | head -1)

echo "REMOTE-INFO: Determine chroot from /var/lib/copr-rpmbuild/main.log"
MOCK_CHROOT=$(grep -Po 'INFO: Start\([^\)]+\)\s+Config\(\K[^\)]+' /var/lib/copr-rpmbuild/main.log | head -n1)

echo "REMOTE-INFO: Prepare /var/lib/mock to contain directories without mock uniqueext ($mock_uniqueext)"
pushd /var/lib/mock/
if [[ ! -e /var/lib/mock/$MOCK_CHROOT ]]; then
  ln -sv $MOCK_CHROOT-$mock_uniqueext $MOCK_CHROOT
fi
if [[ ! -e /var/lib/mock/$MOCK_CHROOT-bootstrap ]]; then
  ln -sfv $MOCK_CHROOT-bootstrap-$mock_uniqueext $MOCK_CHROOT-bootstrap
fi
popd
ls -lha /var/lib/mock

echo "REMOTE-INFO: Setup tmux config to support mouse scrolling and some more"
mkdir -pv ~/.config/tmux
cat << EOF > ~/.config/tmux/tmux.conf
# Options to make tmux more pleasant
set -g mouse on
set -g default-terminal "tmux-256color"

# Start windows and panes at 1, not 0
set -g base-index 1
setw -g pane-base-index 1

set -g status-position top
set -g history-file ~/.tmux_history
EOF
