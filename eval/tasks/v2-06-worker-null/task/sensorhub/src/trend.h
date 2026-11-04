/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TREND_H
#define TREND_H

#include <stdio.h>
#include "sensorhub.h"

#define TREND_LEN 16

void trend_reset(void);
void trend_add(const struct sensor *s, double value);
void trend_report(FILE *out);

#endif
