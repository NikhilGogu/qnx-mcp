/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "core/clock.h"
#include "core/crc.h"
#include "net/udp.h"
#include "telem.h"

int main(int argc, char **argv)
{
    const char *dest = "7100";
    int count = 10, interval = 100, opt, s;
    struct sockaddr_in dst;

    while ((opt = getopt(argc, argv, "p:n:i:")) != -1) {
        if (opt == 'p')
            dest = optarg;
        else if (opt == 'n')
            count = atoi(optarg);
        else if (opt == 'i')
            interval = atoi(optarg);
    }
    s = udp_sender(dest, &dst);
    if (s == -1) {
        perror("telemd: socket");
        return 1;
    }
    for (int i = 0; i < count; i++) {
        struct telem_pkt p = { htole32(TELEM_MAGIC), htole32((uint32_t)i), htole32((uint32_t)clock_uptime_s()),
                               (int32_t)htole32((uint32_t)(int32_t)(sampler_next(i) * 1000)), 0 };

        p.crc = htole32(crc32_ieee(&p, sizeof p - sizeof p.crc));
        if (udp_send(s, &dst, &p, sizeof p) != 0) {
            perror("telemd: send");
            return 1;
        }
        clock_sleep_ms((unsigned)interval);
    }
    printf("telemd: sent %d packets to port %s\n", count, dest);
    return 0;
}
