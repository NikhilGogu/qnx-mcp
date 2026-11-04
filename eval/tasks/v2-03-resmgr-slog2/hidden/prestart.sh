#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Field unit state for v2-03: acqd from the shipped source is running with the
# unit's board description and the start-up script has run acqctl apply.
. ./oracle_lib.sh
cp -a "$SRC" ./build-src && ( cd build-src && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && make ) > prestart-build.log 2>&1 || exit 1
tssh 20 'mkdir -p /data/opt/acq' || exit 1
scp -q "${SSHO[@]}" build-src/acqd build-src/acqctl "root@$IP:/data/opt/acq/" || exit 1
tssh 20 'cd /data/opt/acq && (./acqd > /dev/null 2>&1 &) && sleep 0.5 && ./acqctl apply > /dev/null 2>&1; exit 0'
