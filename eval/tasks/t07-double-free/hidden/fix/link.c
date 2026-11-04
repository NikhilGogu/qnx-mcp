/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include "packet.h"

struct packet *packet_new(int seq, size_t len)
{
    struct packet *p = malloc(sizeof *p);

    if (p == NULL)
        return NULL;
    p->data = malloc(len);
    if (p->data == NULL) {
        free(p);
        return NULL;
    }
    p->seq = seq;
    p->len = len;
    for (size_t i = 0; i < len; i++)
        p->data[i] = (unsigned char)(seq * 31 + i);
    return p;
}

void packet_free(struct packet *p)
{
    free(p->data);
    free(p);
}

int link_send(struct link *l, struct packet *p)
{
    l->sends++;
    if (l->fail_every > 0 && l->sends % l->fail_every == 0) {
        l->failures++;
        return -1;
    }
    for (size_t i = 0; i < p->len; i++)
        l->checksum = (l->checksum * 131 + p->data[i]) % 1000000007UL;
    packet_free(p);
    return 0;
}
