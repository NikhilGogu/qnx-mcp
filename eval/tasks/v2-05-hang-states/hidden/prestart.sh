#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Field unit state for v2-05: the shipped pipeline is running on the unit's
# configuration and input (and is stuck).
. ./oracle_lib.sh
cp -a "$SRC" ./build-src && ( cd build-src && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && make ) > prestart-build.log 2>&1 || exit 1
tssh 20 'mkdir -p /data/opt/pipeline' || exit 1
scp -q "${SSHO[@]}" build-src/pipeline "root@$IP:/data/opt/pipeline/" || exit 1
tssh 20 'cd /data/opt/pipeline && (./pipeline > /data/var/pipeline/pipeline.out 2>&1 &) ; sleep 1; exit 0'
