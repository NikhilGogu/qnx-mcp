/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <string.h>
#include "filter.h"

static const char *const names[] = { "none", "avg", "median", "iir", "decim" };

int filter_from_name(const char *s)
{
    for (int i = 0; i < (int)(sizeof names / sizeof names[0]); i++)
        if (strcmp(names[i], s) == 0)
            return i;
    return -1;
}

const char *filter_name(int kind)
{
    return kind >= 0 && kind < (int)(sizeof names / sizeof names[0]) ? names[kind] : "?";
}

/* The window has been validated by the configuration loader. */
void filter_init(struct filter_state *f, int kind, int window, double alpha)
{
    memset(f, 0, sizeof *f);
    f->kind = kind;
    f->window = window;
    f->alpha = alpha;
}

static double median_of(const double *b, int n)
{
    int k = n / 2;

    for (int i = 0; i < n; i++) {
        int less = 0, equal = 0;

        for (int j = 0; j < n; j++) {
            if (b[j] < b[i])
                less++;
            else if (b[j] == b[i])
                equal++;
        }
        if (less <= k && k < less + equal)
            return b[i];
    }
    return b[0];
}

int filter_apply(struct filter_state *f, double x, double *out)
{
    switch (f->kind) {
    case F_NONE:
        *out = x;
        return 1;
    case F_AVG:
        if (f->count == f->window)
            f->acc -= f->buf[f->pos];
        else
            f->count++;
        f->buf[f->pos] = x;
        f->acc += x;
        f->pos = (f->pos + 1) % f->window;
        *out = f->acc / f->count;
        return 1;
    case F_MEDIAN:
        f->buf[f->pos] = x;
        f->pos = (f->pos + 1) % f->window;
        if (f->count < f->window)
            f->count++;
        *out = median_of(f->buf, f->count);
        return 1;
    case F_IIR:
        if (f->count++ == 0)
            f->acc = x;
        else
            f->acc = f->alpha * x + (1.0 - f->alpha) * f->acc;
        *out = f->acc;
        return 1;
    case F_DECIM:
        f->acc += x;
        if (++f->count < f->window)
            return 0;
        *out = f->acc / f->count;
        f->acc = 0.0;
        f->count = 0;
        return 1;
    }
    return 0;
}
