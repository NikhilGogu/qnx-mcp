#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-07-cmake-cross: fresh QNX cross build with the project's own
# toolchain file, a Linux build that must still work, and the two programs
# talking over loopback on the target.
. ./oracle_lib.sh
rm -rf bq bl
( source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && cmake -S src -B bq -DCMAKE_TOOLCHAIN_FILE="$PWD/src/cmake/qnx-x86_64.cmake" && cmake --build bq ) > qnx-build.log 2>&1
check "QNX cross build" $? "$(grep -E 'error|Error' qnx-build.log | head -3 | tr '\n' ' ')"
[ -x bq/apps/telemd/telemd ] && [ -x bq/tools/tlmdump/tlmdump ]
check "QNX binaries built" $? || finish
( cmake -S src -B bl && cmake --build bl ) > linux-build.log 2>&1
check "Linux build still works" $? "$(grep -E 'error|Error' linux-build.log | head -3 | tr '\n' ' ')"
stage bq/apps/telemd/telemd bq/tools/tlmdump/tlmdump
printf 'telemd: sent 5 packets to port 7100\ntlmdump: received 5 packets, 0 bad, seq 0..4\n' > e1
expect_out "telemetry over loopback on the target" 30 0 e1 'cd /tmp/oracle && (./tlmdump -p 7100 -n 5 -t 5000 > dump.out 2>&1 &) ; sleep 0.5; ./telemd -p 7100 -n 5 -i 20 && sleep 0.5 && cat dump.out'
printf 'telemd: sent 3 packets to port 127.0.0.1:7101\ntlmdump: received 3 packets, 0 bad, seq 0..2\n' > e2
expect_out "host:port form" 30 0 e2 'cd /tmp/oracle && (./tlmdump -p 127.0.0.1:7101 -n 3 -t 5000 > dump2.out 2>&1 &) ; sleep 0.5; ./telemd -p 127.0.0.1:7101 -n 3 -i 10 && sleep 0.5 && cat dump2.out'
finish
