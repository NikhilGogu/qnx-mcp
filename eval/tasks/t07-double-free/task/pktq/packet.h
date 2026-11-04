/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef PACKET_H
#define PACKET_H

#include <stddef.h>

struct packet {
    int seq;
    size_t len;
    unsigned char *data;
};

struct link {
    int sends;
    int fail_every;
    int failures;
    unsigned long checksum;
};

struct packet *packet_new(int seq, size_t len);
void packet_free(struct packet *p);
int link_send(struct link *l, struct packet *p);

#endif
