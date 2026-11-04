/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef NET_ADDR_H
#define NET_ADDR_H

#include <netinet/in.h>

int addr_parse(const char *spec, struct sockaddr_in *out);

#endif
