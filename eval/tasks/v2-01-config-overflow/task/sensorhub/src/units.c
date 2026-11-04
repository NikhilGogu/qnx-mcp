/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <string.h>
#include "units.h"

static const struct unit_info units[] = {
    { "C", ST_TEMP, 1.0, 0.0 },
    { "F", ST_TEMP, 1.8, 32.0 },
    { "K", ST_TEMP, 1.0, 273.15 },
    { "hPa", ST_PRESS, 1.0, 0.0 },
    { "kPa", ST_PRESS, 0.1, 0.0 },
    { "Pa", ST_PRESS, 100.0, 0.0 },
    { "%RH", ST_HUMID, 1.0, 0.0 },
    { "g", ST_ACCEL, 1.0, 0.0 },
    { "m/s2", ST_ACCEL, 9.80665, 0.0 },
    { "mV", ST_VOLT, 1.0, 0.0 },
    { "V", ST_VOLT, 0.001, 0.0 },
    { "%", ST_RATIO, 1.0, 0.0 },
};

const struct unit_info *unit_find(const char *name)
{
    for (size_t i = 0; i < sizeof units / sizeof units[0]; i++)
        if (strcmp(units[i].name, name) == 0)
            return &units[i];
    return NULL;
}

const struct unit_info *unit_default(int type)
{
    for (size_t i = 0; i < sizeof units / sizeof units[0]; i++)
        if (units[i].quantity == type)
            return &units[i];
    return NULL;
}

double unit_convert(const struct unit_info *u, double natural)
{
    return natural * u->scale + u->offset;
}
