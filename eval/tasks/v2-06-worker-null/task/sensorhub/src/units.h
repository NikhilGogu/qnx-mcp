/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef UNITS_H
#define UNITS_H

#include "sensorhub.h"

const struct unit_info *unit_find(const char *name);
const struct unit_info *unit_default(int type);
double unit_convert(const struct unit_info *u, double natural);

#endif
