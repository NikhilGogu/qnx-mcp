/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef NET_UDP_H
#define NET_UDP_H

#include <stddef.h>
#include <netinet/in.h>

int udp_sender(const char *spec, struct sockaddr_in *dst);
int udp_receiver(const char *spec, int timeout_ms);
int udp_send(int s, const struct sockaddr_in *dst, const void *buf, size_t len);
int udp_recv(int s, void *buf, size_t len);

#endif
