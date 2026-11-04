#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t08-no-timeout: normal and slow start-ups succeed; a worker that
# exits early or stalls is given up on after about 2 s with status 3, and a
# stalled worker is killed.
. ./oracle_lib.sh
build src supervisor worker || finish
stage src/supervisor src/worker hidden/worker.conf hidden/stall.conf hidden/slow.conf

# run_sup <name> <expected rc> <min s> <max s> <config> <expected line>...
run_sup() {
    local name=$1 erc=$2 lo=$3 hi=$4 cfg=$5 t0 t1 out rc el miss=""; shift 5
    t0=$(date +%s.%N)
    out=$(tssh 20 "cd /tmp/oracle && ./supervisor $cfg" 2>/dev/null)
    rc=$?
    t1=$(date +%s.%N)
    el=$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.2f", b - a }')
    for want in "$@"; do printf '%s\n' "$out" | grep -qxF "$want" || miss="$miss[$want]"; done
    if [ "$rc" != "$erc" ] || [ -n "$miss" ] || awk -v e="$el" -v lo="$lo" -v hi="$hi" 'BEGIN { exit !(e < lo || e > hi) }'; then
        check "$name" 1 "exit $rc (want $erc), ${el}s (want $lo..$hi), missing $miss; stdout: $(printf '%s' "$out" | tr '\n' '|')"
    else
        check "$name" 0
    fi
}
run_sup "normal start" 0 0 6 worker.conf "supervisor: worker ready" "supervisor: worker exited with status 0"
run_sup "slow start (0.8 s)" 0 0.5 6 slow.conf "supervisor: worker ready" "supervisor: worker exited with status 0"
run_sup "worker cannot start" 3 0 6 /tmp/oracle/missing.conf "supervisor: worker not ready after 2 s"
run_sup "worker stalls" 3 1.7 6 stall.conf "supervisor: worker not ready after 2 s"
left=$(tssh 10 'pidin -p worker -f a 2>/dev/null | tail -n +2 | wc -l')
[ "${left// /}" = 0 ]
check "stalled worker was killed" $? "$left worker process(es) left"
finish
