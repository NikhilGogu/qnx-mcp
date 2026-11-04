/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
/* Oracle probe: checks /dev/acq0's devctl answers against the documented contract. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include "acq.h"

int main(int argc, char **argv)
{
    int fd = open(ACQ_PATH, O_RDWR);

    if (fd == -1)
        return 100;
    for (int i = 1; i + 1 < argc; i += 2) {
        char c = argv[i][0];
        long v = strtol(argv[i + 1], NULL, 10);
        uint32_t u = (uint32_t)v;
        int32_t s = (int32_t)v;
        int dcmd = c == 'r' ? DCMD_ACQ_SET_RATE : c == 'g' ? DCMD_ACQ_SET_GAIN : c == 'c' ? DCMD_ACQ_SET_COUPLING : DCMD_ACQ_SET_TRIGGER;
        int rc = c == 't' ? devctl(fd, dcmd, &s, sizeof s, NULL) : devctl(fd, dcmd, &u, sizeof u, NULL);

        printf("%c %ld -> %d\n", c, v, rc);
    }
    return 0;
}
