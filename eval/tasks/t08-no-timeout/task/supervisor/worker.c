/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/neutrino.h>
#include "ready.h"

static void sleep_ms(long ms)
{
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };

    nanosleep(&ts, NULL);
}

int main(int argc, char **argv)
{
    long delay_ms = 0, work_ms = 200;
    char line[128];
    FILE *f;
    int coid;

    if (argc < 4) {
        fprintf(stderr, "usage: worker <supervisor-pid> <chid> <config>\n");
        return 1;
    }
    f = fopen(argv[3], "r");
    if (f == NULL) {
        fprintf(stderr, "worker: cannot open %s\n", argv[3]);
        return 1;
    }
    while (fgets(line, sizeof line, f)) {
        sscanf(line, "startup_delay_ms=%ld", &delay_ms);
        sscanf(line, "work_ms=%ld", &work_ms);
    }
    fclose(f);
    sleep_ms(delay_ms);
    coid = ConnectAttach(0, (pid_t)atoi(argv[1]), atoi(argv[2]), _NTO_SIDE_CHANNEL, 0);
    if (coid == -1) {
        perror("worker: ConnectAttach");
        return 1;
    }
    MsgSendPulse(coid, -1, PULSE_READY, 0);
    sleep_ms(work_ms);
    return 0;
}
