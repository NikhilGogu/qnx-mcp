/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include "net/addr.h"
#include "net/udp.h"

int udp_sender(const char *spec, struct sockaddr_in *dst)
{
    if (addr_parse(spec, dst) != 0)
        return -1;
    return socket(AF_INET, SOCK_DGRAM, 0);
}

int udp_receiver(const char *spec, int timeout_ms)
{
    struct sockaddr_in a;
    struct timeval tv = { timeout_ms / 1000, (timeout_ms % 1000) * 1000 };
    int s;

    if (addr_parse(spec, &a) != 0)
        return -1;
    s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s == -1)
        return -1;
    if (bind(s, (struct sockaddr *)&a, sizeof a) == -1 || setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv) == -1) {
        close(s);
        return -1;
    }
    return s;
}

int udp_send(int s, const struct sockaddr_in *dst, const void *buf, size_t len)
{
    return sendto(s, buf, len, 0, (const struct sockaddr *)dst, sizeof *dst) == (ssize_t)len ? 0 : -1;
}

int udp_recv(int s, void *buf, size_t len)
{
    return (int)recv(s, buf, len, 0);
}
