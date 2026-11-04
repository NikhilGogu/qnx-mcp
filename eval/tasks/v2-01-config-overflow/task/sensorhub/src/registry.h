/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef REGISTRY_H
#define REGISTRY_H

#include "sensorhub.h"

int registry_build(const struct sensor_cfg *cfgs, int n, int *rejected);
int registry_count(void);
struct sensor *registry_get(int i);
struct sensor *registry_find(const char *name);

#endif
