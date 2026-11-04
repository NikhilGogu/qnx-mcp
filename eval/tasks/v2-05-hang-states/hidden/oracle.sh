#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-05-hang-states: the unit's run must finish with every record
# written, and two unseen configurations (partial last batch, batch 1 with a
# rate limit and a bad line) must too.
. ./oracle_lib.sh
build src pipeline || finish
stage src/pipeline
scp -r -q "${SSHO[@]}" hidden/set2 "root@$IP:/tmp/oracle/"
printf 'pipeline: read 1003 written 1003 bad 0 batches 126\n' > e1
expect_out "unit configuration (1003 records, batch 8, 3 workers)" 30 0 e1 'cd /tmp/oracle && ./pipeline'
s1=$(tssh 10 'sum /data/var/pipeline/output.dat 2>/dev/null; wc -l < /data/var/pipeline/output.dat')
want1=$(awk -F, '{printf "%d,%d\n", $1, $2 * 3 + 7}' hidden/target/data/var/pipeline/input.dat | wc -l)
[ "$(echo "$s1" | tail -1 | tr -d ' ')" = "$want1" ]
check "unit output has every record" $? "$s1"
printf 'pipeline: read 77 written 77 bad 1 batches 8\n' > e2
expect_out "unseen: 77 records, batch 10, 4 workers" 30 0 e2 'cd /tmp/oracle && ./pipeline /tmp/oracle/set2/a.conf'
printf 'pipeline: read 77 written 77 bad 1 batches 77\n' > e3
expect_out "unseen: batch 1, 2 workers, rate limit" 30 0 e3 'cd /tmp/oracle && ./pipeline /tmp/oracle/set2/b.conf'
awk -F, 'NF == 2 {printf "%d,%d\n", $1, $2 * 3 + 7}' hidden/set2/input.dat > want_a
got=$(tssh 10 'cat /tmp/oracle/set2/out_a.dat' | sort)
diff <(printf '%s\n' "$got") <(sort want_a) > /dev/null
check "unseen output values" $? "$(diff <(printf '%s\n' "$got") <(sort want_a) | head -3 | tr '\n' '|')"
finish
