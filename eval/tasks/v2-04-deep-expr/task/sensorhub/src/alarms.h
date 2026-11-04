/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef ALARMS_H
#define ALARMS_H

#include <stdio.h>
#include "sensorhub.h"

struct alarm_rule {
    char sensor[NAME_LEN];
    double lo;
    double hi;
    double hysteresis;
    int has_lo;
    int has_hi;
};

int alarms_load(const char *dir);
void alarms_bind(void);
void alarms_check(const struct sensor *s, double value, int cycle);
void alarms_report(FILE *out);
int alarms_count(void);

#endif
