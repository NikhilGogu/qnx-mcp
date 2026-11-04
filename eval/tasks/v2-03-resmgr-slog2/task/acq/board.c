/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "board.h"

/* Board description written at manufacturing: key=value lines. */
int board_load(const char *path, struct acq_limits *lim, int *rev)
{
    char line[128];
    FILE *f = fopen(path, "r");

    lim->max_rate_hz = 10000;
    lim->max_gain = 16;
    lim->trig_min_mv = -5000;
    lim->trig_max_mv = 5000;
    lim->has_ac = 1;
    lim->has_trigger = 1;
    if (f == NULL)
        return -1;
    while (fgets(line, sizeof line, f)) {
        char key[32], r;
        long v;

        if (sscanf(line, " rev = %c", &r) == 1) {
            *rev = r;
            continue;
        }
        if (sscanf(line, " %31[a-z_] = %ld", key, &v) != 2)
            continue;
        if (strcmp(key, "max_rate_hz") == 0)
            lim->max_rate_hz = (uint32_t)v;
        else if (strcmp(key, "max_gain") == 0)
            lim->max_gain = (uint32_t)v;
        else if (strcmp(key, "trig_min_mv") == 0)
            lim->trig_min_mv = (int32_t)v;
        else if (strcmp(key, "trig_max_mv") == 0)
            lim->trig_max_mv = (int32_t)v;
        else if (strcmp(key, "has_ac") == 0)
            lim->has_ac = v != 0;
        else if (strcmp(key, "has_trigger") == 0)
            lim->has_trigger = v != 0;
    }
    fclose(f);
    return 0;
}
