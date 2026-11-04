#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-03-resmgr-slog2: the unit's configuration must end with the
# trigger clamped and armed; a direct devctl probe checks the documented errno
# contract on the unit's board and on a board without trigger or ac coupling.
. ./oracle_lib.sh
build src acqd acqctl || finish
cp src/acq.h hidden/ && ( cd hidden && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && qcc -Vgcc_ntox86_64 -o probe probe.c ) > probe.log 2>&1
check "build probe" $? "$(tail -3 probe.log | tr '\n' ' ')"
stage src/acqd src/acqctl hidden/probe hidden/notrig.board hidden/low.conf
restart() { tssh 15 "slay -f acqd >/dev/null 2>&1; sleep 0.3; cd /tmp/oracle && (./acqd $1 > /dev/null 2>&1 &) && sleep 0.5; exit 0"; }
restart ""
printf 'acqctl: configuration applied\n' > e1
expect_out "apply the unit configuration" 15 0 e1 'cd /tmp/oracle && ./acqctl apply'
printf 'acq: running rate 2000 Hz gain 8 coupling dc trigger 2500 mV\n' > e2
expect_out "unit status: trigger clamped to 2500 mV" 15 0 e2 'cd /tmp/oracle && ./acqctl status'
printf 't 2600 -> 34\nt -2600 -> 34\nt 2500 -> 0\nc 1 -> 48\nr 6000 -> 34\ng 32 -> 34\ng 3 -> 22\n' > e3
expect_out "devctl contract on the unit board" 15 0 e3 'cd /tmp/oracle && ./acqctl status >/dev/null && ./probe t 2600 t -2600 t 2500 c 1 r 6000 g 32 g 3'
restart "-b /tmp/oracle/notrig.board"
printf 'acqctl: configuration applied\n' > e4
expect_out "apply on a board without trigger" 15 0 e4 'cd /tmp/oracle && ./acqctl apply /tmp/oracle/low.conf'
printf 'acq: running rate 5000 Hz gain 8 coupling ac trigger off\n' > e5
expect_out "status on a board without trigger" 15 0 e5 'cd /tmp/oracle && ./acqctl status'
printf 't 100 -> 48\n' > e6
expect_out "no trigger input means ENOTSUP" 15 0 e6 'cd /tmp/oracle && ./probe t 100'
finish
