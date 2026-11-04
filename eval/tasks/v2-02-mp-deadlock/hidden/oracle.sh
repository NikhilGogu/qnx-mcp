#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-02-mp-deadlock: with the unit's configuration (quota checked by
# the gateway) records must flow and the quota must be enforced; local and
# zero quotas must behave as documented.
. ./oracle_lib.sh
build src gatewayd storaged feed || finish
stage src/gatewayd src/storaged src/feed hidden/storaged-local.conf hidden/gateway-zero.conf
start() {  # start <storaged conf> <gateway conf>
    tssh 15 "slay -f -s KILL storaged gatewayd feed >/dev/null 2>&1; rm -f /tmp/oracle/records.log; cd /tmp/oracle && (./storaged -c $1 -l /tmp/oracle/records.log > /tmp/oracle/s.out 2>&1 &) && sleep 0.5 && (./gatewayd -c $2 > /tmp/oracle/g.out 2>&1 &) && sleep 0.5; exit 0"
}
start /etc/storaged.conf /etc/gateway.conf
printf 'feed: sent 50 stored 30 rejected 20\n' > exp1
expect_out "unit configuration: quota from the gateway" 20 0 exp1 'cd /tmp/oracle && ./feed 50'
printf 'feed: sent 5 stored 0 rejected 5\n' > exp2
expect_out "gateway quota used up" 20 0 exp2 'cd /tmp/oracle && ./feed 5'
n=$(tssh 10 'wc -l < /tmp/oracle/records.log')
[ "${n// /}" = 30 ]
check "30 records stored" $? "log has $n lines"
start /tmp/oracle/storaged-local.conf /etc/gateway.conf
printf 'feed: sent 10 stored 7 rejected 3\n' > exp3
expect_out "local quota" 20 0 exp3 'cd /tmp/oracle && ./feed 10'
start /etc/storaged.conf /tmp/oracle/gateway-zero.conf
printf 'feed: sent 3 stored 0 rejected 3\n' > exp4
expect_out "gateway quota zero" 20 0 exp4 'cd /tmp/oracle && ./feed 3'
finish
