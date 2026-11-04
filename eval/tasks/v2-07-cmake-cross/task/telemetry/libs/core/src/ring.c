/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "core/ring.h"

void ring_init(struct ring *r, int cap)
{
    r->cap = cap < 1 ? 1 : cap > RING_MAX ? RING_MAX : cap;
    r->n = r->pos = 0;
}

void ring_push(struct ring *r, double v)
{
    r->v[r->pos] = v;
    r->pos = (r->pos + 1) % r->cap;
    if (r->n < r->cap)
        r->n++;
}

double ring_mean(const struct ring *r)
{
    double s = 0.0;

    for (int i = 0; i < r->n; i++)
        s += r->v[i];
    return r->n ? s / r->n : 0.0;
}
