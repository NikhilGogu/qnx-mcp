/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "stats.h"

void stats_reset(struct stats *st)
{
    st->n = 0;
    st->sum = 0.0;
    st->min = 0.0;
    st->max = 0.0;
    st->last = 0.0;
}

void stats_update(struct stats *st, double v)
{
    if (st->n == 0 || v < st->min)
        st->min = v;
    if (st->n == 0 || v > st->max)
        st->max = v;
    st->sum += v;
    st->last = v;
    st->n++;
}

double stats_mean(const struct stats *st)
{
    return st->n ? st->sum / (double)st->n : 0.0;
}
