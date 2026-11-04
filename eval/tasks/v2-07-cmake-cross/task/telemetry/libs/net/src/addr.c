/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "net/addr.h"

/* "port" or "a.b.c.d:port"; the host defaults to the loopback address. */
int addr_parse(const char *spec, struct sockaddr_in *out)
{
    char host[64] = "127.0.0.1";
    const char *colon = strchr(spec, ':');
    long port;

    memset(out, 0, sizeof *out);
    if (colon) {
        size_t n = (size_t)(colon - spec);

        if (n == 0 || n >= sizeof host)
            return -1;
        memcpy(host, spec, n);
        host[n] = '\0';
        spec = colon + 1;
    }
    port = strtol(spec, NULL, 10);
    if (port <= 0 || port > 65535)
        return -1;
    out->sin_family = AF_INET;
    out->sin_port = htons((unsigned short)port);
    return inet_pton(AF_INET, host, &out->sin_addr) == 1 ? 0 : -1;
}
