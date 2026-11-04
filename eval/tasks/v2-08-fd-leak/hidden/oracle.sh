#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-08-fd-leak: after a long poll of the unit's components
# (including its malformed pump.state) every component must still answer and
# statusd must not hold state files open; unseen malformed files too.
. ./oracle_lib.sh
build src statusd statusctl || finish
stage src/statusd src/statusctl
tar cf - -C hidden set2 | tssh 20 'tar xf - -C /tmp/oracle'
check "stage the unseen state files (with their symlink)" $?
tssh 15 'cd /tmp/oracle && (./statusd > /dev/null 2>&1 &) ; sleep 0.5; exit 0'
expect_out "300 polls of the unit's components" 120 0 - 'cd /tmp/oracle && i=0; while [ $i -lt 75 ]; do for c in pump fan valve heater; do ./statusctl $c > /dev/null || exit 12; done; i=$((i+1)); done; exit 0'
printf 'pump: running since 1793712000 code 0 [pump.state.v42]\nfan: running since 1793711000 code 0\nvalve: open since 1793713000 code 2\nheater: idle since 1793714000 code 0\n' > e1
expect_out "components after the poll" 20 0 e1 'cd /tmp/oracle && ./statusctl pump && ./statusctl fan && ./statusctl valve && ./statusctl heater'
open=$(tssh 10 'pidin -p statusd fds 2>/dev/null | grep -c "\.state"')
[ "${open// /}" = 0 ]
check "statusd holds no state files open" $? "$open open"
tssh 15 'slay -f statusd >/dev/null 2>&1; sleep 0.3; cd /tmp/oracle && (./statusd -d /tmp/oracle/set2 > /dev/null 2>&1 &) ; sleep 0.5; exit 0'
expect_out "unseen files, 60 rounds" 120 0 - 'cd /tmp/oracle && i=0; while [ $i -lt 60 ]; do for c in a b c; do ./statusctl $c 2>/dev/null; [ $? = 3 ] || exit 13; done; ./statusctl e > /dev/null || exit 14; i=$((i+1)); done; exit 0'
printf 'd: ok since 5 code 1\ne: ok since 7 code 0 [e.state.3]\n' > e2
expect_out "good files after the unseen ones" 20 0 e2 'cd /tmp/oracle && ./statusctl d && ./statusctl e'
finish
