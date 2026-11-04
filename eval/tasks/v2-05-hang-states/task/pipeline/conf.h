/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CONF_H
#define CONF_H

struct pipeline_conf {
    char input[256];
    char output[256];
    int batch;
    int depth;
    int rate_limit_ms;
    int workers;
};

int conf_load(const char *path, struct pipeline_conf *c);

#endif
