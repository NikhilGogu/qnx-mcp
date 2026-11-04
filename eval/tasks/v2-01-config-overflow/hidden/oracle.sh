#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for v2-01-config-overflow: the unit's configuration (installed in /etc/sensor.d by the
# harness), an unseen configuration and the bench configuration; exact report.
. ./oracle_lib.sh
build src sensorhub || finish
stage src/sensorhub
scp -r -q "${SSHO[@]}" hidden/set2 hidden/bench "root@$IP:/tmp/oracle/"
NUMTOL=0.0011
expect_out "unit configuration in /etc/sensor.d" 120 0 hidden/expected/target.out 'cd /tmp/oracle && ./sensorhub'
expect_out "unseen configuration" 120 0 hidden/expected/set2.out 'cd /tmp/oracle && ./sensorhub -c /tmp/oracle/set2 -n 40'
expect_out "bench configuration" 120 0 hidden/expected/bench.out 'cd /tmp/oracle && ./sensorhub -c /tmp/oracle/bench'
finish
