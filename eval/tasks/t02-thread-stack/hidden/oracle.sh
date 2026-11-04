#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t02-thread-stack: default run and two unseen seeds/sizes must
# match the Linux results exactly.
. ./oracle_lib.sh
build src vibmon || finish
stage src/vibmon
expect_out "default run" 30 0 hidden/expected/default.out 'cd /tmp/oracle && ./vibmon'
expect_out "seed 7, 100000 samples" 30 0 hidden/expected/s7.out 'cd /tmp/oracle && ./vibmon 7 100000'
expect_out "seed 3, 1000 samples" 30 0 hidden/expected/s3.out 'cd /tmp/oracle && ./vibmon 3 1000'
finish
