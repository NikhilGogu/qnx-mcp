/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "gw.h"

int main(int argc, char **argv)
{
    int count = argc > 1 ? atoi(argv[1]) : 10;
    int coid = name_open(GW_NAME, 0), ok = 0, refused = 0;

    if (coid == -1) {
        perror("feed: name_open " GW_NAME);
        return 1;
    }
    for (int i = 0; i < count; i++) {
        struct record_msg rec = { .type = MSG_RECORD, .seq = (uint32_t)i };
        struct store_reply rep;

        snprintf(rec.payload, sizeof rec.payload, "reading %d value %d", i, (i * 37) % 101);
        if (MsgSend(coid, &rec, sizeof rec, &rep, sizeof rep) == -1) {
            fprintf(stderr, "feed: record %d: %s\n", i, strerror(errno));
            return 1;
        }
        if (rep.status == EOK)
            ok++;
        else
            refused++;
    }
    printf("feed: sent %d stored %d rejected %d\n", count, ok, refused);
    name_close(coid);
    return 0;
}
