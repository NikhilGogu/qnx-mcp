#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t04-deadlock: three default runs and one unseen count must all
# finish in time with exact balances.
. ./oracle_lib.sh
build src ledger || finish
stage src/ledger
for i in 1 2 3; do
    expect_out "default run $i" 40 0 hidden/expected/n500.out 'cd /tmp/oracle && ./ledger'
done
expect_out "200 transfers" 40 0 hidden/expected/n200.out 'cd /tmp/oracle && ./ledger 200'
finish
