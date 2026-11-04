/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <string.h>
#include "calib.h"

static const struct {
    const char *name;
    struct cal_table table;
} cal_db[] = {
    { "temp0",  { 0.125, -40.0 } },
    { "press1", { 0.1,     0.0 } },
    { "temp3",  { 0.125, -41.5 } },
};

const struct cal_table *calib_find(const char *name)
{
    for (size_t i = 0; i < sizeof cal_db / sizeof cal_db[0]; i++)
        if (strcmp(cal_db[i].name, name) == 0)
            return &cal_db[i].table;
    return NULL;
}

double calib_apply(const struct sensor *s, int raw)
{
    if (s->cal == NULL)
        return raw;
    return raw * s->cal->gain + s->cal->offset;
}

double sensor_average(const struct sensor *s)
{
    double sum = 0.0;

    for (int i = 0; i < s->nsamples; i++)
        sum += calib_apply(s, s->samples[i]);
    return s->nsamples ? sum / s->nsamples : 0.0;
}
