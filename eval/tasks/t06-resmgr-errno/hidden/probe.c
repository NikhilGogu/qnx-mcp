/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
/* Oracle probe: checks sensord's devctl contract directly. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "sensor.h"

int main(int argc, char **argv)
{
    int fd = open(SENSOR_PATH, O_RDWR), rc;
    uint32_t hz;

    if (fd == -1 || argc < 2)
        return 100;
    if (strcmp(argv[1], "get") == 0) {
        rc = devctl(fd, DCMD_SENSOR_GET_RATE, &hz, sizeof hz, NULL);
        printf("get rc=%d hz=%u\n", rc, rc == 0 ? hz : 0);
    } else {
        hz = (uint32_t)strtoul(argv[2], NULL, 10);
        rc = devctl(fd, DCMD_SENSOR_SET_RATE, &hz, sizeof hz, NULL);
        printf("set %s rc=%d\n", argv[2], rc);
    }
    return 0;
}
