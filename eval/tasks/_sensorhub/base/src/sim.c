/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "sim.h"

/* Deterministic stand-in for the bus hardware: a per-device offset plus a
 * slow triangle wave and a small pseudo-random component, 12-bit range. */
int sim_raw(int bus, int address, int channel, int cycle)
{
    unsigned h = (unsigned)bus * 7919u + (unsigned)address * 104729u + (unsigned)channel * 1299709u;
    unsigned noise = (h ^ ((unsigned)cycle * 2654435761u)) >> 7;
    int base = 600 + (int)(h % 2800);
    int period = 24 + (int)(h % 40);
    int phase = (cycle + (int)(h % 17)) % period;
    int tri = phase < period / 2 ? phase : period - phase;
    int v = base + tri * (2 + (int)(h % 5)) + (int)(noise % 9) - 4;

    if (v < 0)
        v = 0;
    if (v > 4095)
        v = 4095;
    return v;
}
