#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t07-double-free: default and unseen loss patterns, including one
# where every send fails (exercises the drop path); exact output and status.
. ./oracle_lib.sh
build src pktq || finish
stage src/pktq
expect_out "default run" 20 0 hidden/expected/default.out 'cd /tmp/oracle && ./pktq'
expect_out "50 packets, every 3rd send fails" 20 0 hidden/expected/c50f3.out 'cd /tmp/oracle && ./pktq 50 3'
expect_out "10 packets, every 2nd send fails" 20 0 hidden/expected/c10f2.out 'cd /tmp/oracle && ./pktq 10 2'
expect_out "every send fails, all dropped" 20 1 hidden/expected/c5f1.out 'cd /tmp/oracle && ./pktq 5 1'
expect_out "lossless link" 20 0 hidden/expected/lossless.out 'cd /tmp/oracle && ./pktq 20 0'
finish
