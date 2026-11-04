#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Field unit state for v2-02: the services from the shipped source are running
# with the unit's configuration and a feed run is stuck in them.
. ./oracle_lib.sh
cp -a "$SRC" ./build-src && ( cd build-src && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && make ) > prestart-build.log 2>&1 || exit 1
tssh 20 'mkdir -p /data/opt/gateway /data/var/storaged' || exit 1
scp -q "${SSHO[@]}" build-src/gatewayd build-src/storaged build-src/feed "root@$IP:/data/opt/gateway/" || exit 1
tssh 20 'cd /data/opt/gateway && (./storaged > /data/var/storaged/storaged.out 2>&1 &) && sleep 0.5 && (./gatewayd > /data/var/storaged/gatewayd.out 2>&1 &) && sleep 0.5 && (./feed 5 > /data/var/storaged/feed.out 2>&1 &) ; sleep 1; exit 0'
