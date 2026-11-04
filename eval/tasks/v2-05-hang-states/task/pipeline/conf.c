/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <string.h>
#include "conf.h"

int conf_load(const char *path, struct pipeline_conf *c)
{
    char line[400], key[32], val[300];
    FILE *f = fopen(path, "r");

    snprintf(c->input, sizeof c->input, "/data/var/pipeline/input.dat");
    snprintf(c->output, sizeof c->output, "/data/var/pipeline/output.dat");
    c->batch = 1;
    c->depth = 64;
    c->rate_limit_ms = 0;
    c->workers = 1;
    if (f == NULL)
        return -1;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || sscanf(line, " %31[a-z_] = %299s", key, val) != 2)
            continue;
        if (strcmp(key, "input") == 0)
            snprintf(c->input, sizeof c->input, "%s", val);
        else if (strcmp(key, "output") == 0)
            snprintf(c->output, sizeof c->output, "%s", val);
        else if (strcmp(key, "batch") == 0)
            sscanf(val, "%d", &c->batch);
        else if (strcmp(key, "queue_depth") == 0)
            sscanf(val, "%d", &c->depth);
        else if (strcmp(key, "rate_limit_ms") == 0)
            sscanf(val, "%d", &c->rate_limit_ms);
        else if (strcmp(key, "workers") == 0)
            sscanf(val, "%d", &c->workers);
    }
    fclose(f);
    if (c->batch < 1)
        c->batch = 1;
    if (c->depth < c->batch)
        c->depth = c->batch;
    return 0;
}
