/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef BUS_H
#define BUS_H

#include "worker.h"

int bus_setup(struct worker **out);
int bus_sensor_count(int bus);

#endif
