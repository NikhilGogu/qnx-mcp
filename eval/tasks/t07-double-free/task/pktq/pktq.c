/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include "packet.h"

#define MAX_TRIES 3

static int send_with_retry(struct link *l, struct packet *p)
{
    for (int t = 0; t < MAX_TRIES; t++)
        if (link_send(l, p) == 0)
            return 0;
    packet_free(p);
    return -1;
}

int main(int argc, char **argv)
{
    int count = argc > 1 ? atoi(argv[1]) : 20;
    struct link l = { .fail_every = argc > 2 ? atoi(argv[2]) : 4 };
    int delivered = 0, dropped = 0;

    for (int seq = 0; seq < count; seq++) {
        struct packet *p = packet_new(seq, 64 + seq % 7);

        if (p == NULL) {
            fprintf(stderr, "pktq: out of memory\n");
            return 1;
        }
        if (send_with_retry(&l, p) == 0)
            delivered++;
        else
            dropped++;
    }
    printf("pktq: delivered=%d dropped=%d sends=%d failures=%d checksum=%lu\n",
           delivered, dropped, l.sends, l.failures, l.checksum);
    return dropped ? 1 : 0;
}
