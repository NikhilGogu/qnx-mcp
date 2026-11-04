#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t01-calib-null: reported config plus two unseen ones, exact output.
. ./oracle_lib.sh
build src sensor_report || finish
stage src/sensor_report hidden/cfg1 hidden/cfg2 hidden/cfg3
expect_out "reported config" 20 0 hidden/expected/cfg1.out 'cd /tmp/oracle && ./sensor_report cfg1'
expect_out "unseen config 2" 20 0 hidden/expected/cfg2.out 'cd /tmp/oracle && ./sensor_report cfg2'
expect_out "unseen config 3" 20 0 hidden/expected/cfg3.out 'cd /tmp/oracle && ./sensor_report cfg3'
finish
