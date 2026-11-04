/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef EXPR_H
#define EXPR_H

#include "worker.h"

struct derived {
    char name[32];
    struct node *root;
    double value;
    int valid;
};

int derive_setup(const char *dir, struct worker **out);
int derive_count(void);
const struct derived *derive_get(int i);

#endif
