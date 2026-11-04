/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "core/ring.h"
#include "telem.h"

double sampler_next(int seq)
{
    static struct ring r;
    static int ready;

    if (!ready) {
        ring_init(&r, 8);
        ready = 1;
    }
    ring_push(&r, 20.0 + (seq * 37 % 11) * 0.5);
    return ring_mean(&r);
}
