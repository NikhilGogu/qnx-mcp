/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef FILTER_H
#define FILTER_H

#include "sensorhub.h"

int filter_from_name(const char *s);
const char *filter_name(int kind);
void filter_init(struct filter_state *f, int kind, int window, double alpha);
int filter_apply(struct filter_state *f, double x, double *out);

#endif
