/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CALIB_H
#define CALIB_H

struct cal_table {
    double gain;
    double offset;
};

struct sensor {
    char name[16];
    const struct cal_table *cal;
    int nsamples;
    int samples[16];
};

const struct cal_table *calib_find(const char *name);
double calib_apply(const struct sensor *s, int raw);
double sensor_average(const struct sensor *s);

#endif
