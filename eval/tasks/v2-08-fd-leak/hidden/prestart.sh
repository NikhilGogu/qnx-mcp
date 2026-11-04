#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Field unit state for v2-08: the shipped statusd has been running for a while
# and the monitor has been polling it.
. ./oracle_lib.sh
cp -a "$SRC" ./build-src && ( cd build-src && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && make ) > prestart-build.log 2>&1 || exit 1
tssh 20 'mkdir -p /data/opt/status' || exit 1
scp -q "${SSHO[@]}" build-src/statusd build-src/statusctl "root@$IP:/data/opt/status/" || exit 1
tssh 60 'cd /data/opt/status && (./statusd > /dev/null 2>&1 &) && sleep 0.5 && i=0; while [ $i -lt 60 ]; do for c in pump fan valve heater; do ./statusctl $c > /dev/null 2>&1; done; i=$((i+1)); done; exit 0'
