/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "sensor.h"

static const uint32_t supported[] = { 1, 10, 50, 100 };

static uint32_t fallback_rate(uint32_t hz)
{
    uint32_t best = supported[0];

    for (size_t i = 0; i < sizeof supported / sizeof supported[0]; i++)
        if (supported[i] <= hz)
            best = supported[i];
    return best;
}

int main(int argc, char **argv)
{
    uint32_t hz, requested;
    int fd, rc;

    if (!(argc == 2 && strcmp(argv[1], "get-rate") == 0) && !(argc == 3 && strcmp(argv[1], "set-rate") == 0)) {
        fprintf(stderr, "usage: sensorctl get-rate | sensorctl set-rate <hz>\n");
        return 1;
    }
    fd = open(SENSOR_PATH, O_RDWR);
    if (fd == -1) {
        perror("sensorctl: " SENSOR_PATH);
        return 1;
    }
    if (argc == 2) {
        rc = devctl(fd, DCMD_SENSOR_GET_RATE, &hz, sizeof hz, NULL);
        close(fd);
        if (rc != EOK) {
            fprintf(stderr, "sensorctl: get-rate: %s\n", strerror(rc));
            return 1;
        }
        printf("rate: %u Hz\n", hz);
        return 0;
    }
    requested = (uint32_t)strtoul(argv[2], NULL, 10);
    hz = requested;
    rc = devctl(fd, DCMD_SENSOR_SET_RATE, &hz, sizeof hz, NULL);
    if (rc == EINVAL) {
        hz = fallback_rate(requested);
        rc = devctl(fd, DCMD_SENSOR_SET_RATE, &hz, sizeof hz, NULL);
    }
    close(fd);
    if (rc != EOK) {
        fprintf(stderr, "sensorctl: set-rate %u: %s\n", hz, strerror(rc));
        return 1;
    }
    if (hz != requested)
        printf("rate: %u Hz (requested %u)\n", hz, requested);
    else
        printf("rate: %u Hz\n", hz);
    return 0;
}
