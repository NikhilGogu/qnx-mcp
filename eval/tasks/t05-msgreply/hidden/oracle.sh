#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t05-msgreply: missing keys must fail fast with status 2 and the
# server must keep serving afterwards.
. ./oracle_lib.sh
build src cfgsrv cfgctl || finish
stage src/cfgsrv src/cfgctl
tssh 10 'cd /tmp/oracle && ./cfgsrv > srv.log 2>&1 < /dev/null &'
sleep 1
expect_out "get existing key" 10 0 hidden/expected/rate50.out 'cd /tmp/oracle && ./cfgctl get rate'
expect_out "get missing key returns status 2" 10 2 - 'cd /tmp/oracle && ./cfgctl get colour'
err=$(tssh 10 'cd /tmp/oracle && ./cfgctl get colour 2>&1 >/dev/null')
[ "$err" = "cfgctl: no such key: colour" ]
check "missing key message" $? "$err"
expect_out "server still serves" 10 0 hidden/expected/mode.out 'cd /tmp/oracle && ./cfgctl get mode'
expect_out "set existing key" 10 0 hidden/expected/rate100.out 'cd /tmp/oracle && ./cfgctl set rate 100'
expect_out "get after set" 10 0 hidden/expected/rate100.out 'cd /tmp/oracle && ./cfgctl get rate'
expect_out "set missing key returns status 2" 10 2 - 'cd /tmp/oracle && ./cfgctl set colour red'
expect_out "20 missing lookups" 30 0 - 'cd /tmp/oracle && i=0; while [ $i -lt 20 ]; do ./cfgctl get nokey$i 2>/dev/null; [ $? = 2 ] || exit 1; i=$((i+1)); done; exit 0'
expect_out "server healthy after errors" 10 0 hidden/expected/gain.out 'cd /tmp/oracle && ./cfgctl get gain'
left=$(tssh 10 'pidin -p cfgctl -f a 2>/dev/null | tail -n +2 | wc -l')
[ "${left// /}" = 0 ]
check "no cfgctl left blocked" $? "$left still running"
finish
