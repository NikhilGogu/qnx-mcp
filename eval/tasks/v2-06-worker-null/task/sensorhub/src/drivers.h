/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef DRIVERS_H
#define DRIVERS_H

#include "sensorhub.h"

const struct driver_ops *driver_for(int type);

#endif
