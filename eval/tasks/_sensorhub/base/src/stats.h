/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef STATS_H
#define STATS_H

#include "sensorhub.h"

void stats_reset(struct stats *st);
void stats_update(struct stats *st, double v);
double stats_mean(const struct stats *st);

#endif
