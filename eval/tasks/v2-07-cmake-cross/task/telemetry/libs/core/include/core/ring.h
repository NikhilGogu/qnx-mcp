/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CORE_RING_H
#define CORE_RING_H

#define RING_MAX 64

struct ring {
    double v[RING_MAX];
    int cap;
    int n;
    int pos;
};

void ring_init(struct ring *r, int cap);
void ring_push(struct ring *r, double v);
double ring_mean(const struct ring *r);

#endif
