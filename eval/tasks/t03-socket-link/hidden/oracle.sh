#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for t03-socket-link: the task's own Makefile must build it, and the
# server and client must work over loopback on the target.
. ./oracle_lib.sh
build src udpecho || finish
stage src/udpecho
tssh 10 'cd /tmp/oracle && (./udpecho serve 6123 > serve.log 2>&1; echo "rc=$?" >> serve.log) > /dev/null 2>&1 &'
sleep 1
expect_out "echo upper case" 15 0 hidden/expected/hello.out 'cd /tmp/oracle && ./udpecho send 6123 "hello qnx 8"'
expect_out "echo mixed text" 15 0 hidden/expected/mixed.out 'cd /tmp/oracle && ./udpecho send 6123 "Mixed case 123 abc"'
expect_out "quit" 15 0 hidden/expected/bye.out 'cd /tmp/oracle && ./udpecho send 6123 QUIT'
sleep 1
out=$(tssh 10 'cat /tmp/oracle/serve.log')
[ "$out" = "$(printf 'udpecho: served 3 messages\nrc=0')" ]
check "server exits after QUIT" $? "$(printf '%s' "$out" | tr '\n' '|')"
expect_out "no server: client gives up" 15 1 - 'cd /tmp/oracle && ./udpecho send 6124 ping'
finish
