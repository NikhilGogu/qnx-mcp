#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t06-resmgr-errno: sensorctl fallback behaviour plus a direct
# devctl probe of sensord's documented contract (EINVAL for unsupported rates).
. ./oracle_lib.sh
build src sensord sensorctl || finish
cp src/sensor.h hidden/ 2>/dev/null
( cd hidden && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && qcc -Vgcc_ntox86_64 -o probe probe.c ) > probe.log 2>&1
check "build probe" $? "$(tail -3 probe.log | tr '\n' ' ')"
stage src/sensord src/sensorctl hidden/probe
tssh 10 'cd /tmp/oracle && ./sensord > sensord.log 2>&1 < /dev/null &'
sleep 1
expect_out "default rate" 10 0 hidden/expected/r10.out 'cd /tmp/oracle && ./sensorctl get-rate'
expect_out "set 50" 10 0 hidden/expected/r50.out 'cd /tmp/oracle && ./sensorctl set-rate 50'
expect_out "set 75 falls back to 50" 10 0 hidden/expected/r75.out 'cd /tmp/oracle && ./sensorctl set-rate 75'
expect_out "rate after 75" 10 0 hidden/expected/r50.out 'cd /tmp/oracle && ./sensorctl get-rate'
expect_out "set 5 falls back to 1" 10 0 hidden/expected/r5.out 'cd /tmp/oracle && ./sensorctl set-rate 5'
expect_out "rate after 5" 10 0 hidden/expected/r1.out 'cd /tmp/oracle && ./sensorctl get-rate'
expect_out "set 250 falls back to 100" 10 0 hidden/expected/r250.out 'cd /tmp/oracle && ./sensorctl set-rate 250'
expect_out "devctl contract" 20 0 hidden/expected/probe.out 'cd /tmp/oracle && ./probe set 7 && ./probe get && ./probe set 0 && ./probe set 10 && ./probe get'
finish
