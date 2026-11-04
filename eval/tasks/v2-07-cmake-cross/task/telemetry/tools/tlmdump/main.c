/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "core/crc.h"
#include "core/fmt.h"
#include "net/udp.h"
#include "../../apps/telemd/telem.h"

int main(int argc, char **argv)
{
    const char *spec = "7100";
    int count = 10, timeout = 3000, verbose = 0, opt, s, got = 0, bad = 0;
    long first = -1, last = -1;

    while ((opt = getopt(argc, argv, "p:n:t:v")) != -1) {
        if (opt == 'p')
            spec = optarg;
        else if (opt == 'n')
            count = atoi(optarg);
        else if (opt == 't')
            timeout = atoi(optarg);
        else if (opt == 'v')
            verbose = 1;
    }
    s = udp_receiver(spec, timeout);
    if (s == -1) {
        perror("tlmdump: socket");
        return 1;
    }
    while (got + bad < count) {
        struct telem_pkt p;
        int n = udp_recv(s, &p, sizeof p);

        if (n < 0)
            break;
        if (verbose)
            fmt_hexdump(stderr, &p, (size_t)n);
        if (n != (int)sizeof p || le32toh(p.magic) != TELEM_MAGIC || le32toh(p.crc) != crc32_ieee(&p, sizeof p - sizeof p.crc)) {
            bad++;
            continue;
        }
        if (first < 0)
            first = le32toh(p.seq);
        last = le32toh(p.seq);
        got++;
    }
    printf("tlmdump: received %d packets, %d bad, seq %ld..%ld\n", got, bad, first, last);
    return got == count && bad == 0 ? 0 : 1;
}
