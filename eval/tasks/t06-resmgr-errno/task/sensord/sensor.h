/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef SENSOR_H
#define SENSOR_H

#include <devctl.h>
#include <stdint.h>

#define SENSOR_PATH          "/dev/sensor0"
#define DCMD_SENSOR_SET_RATE __DIOT(_DCMD_MISC, 0x41, uint32_t)
#define DCMD_SENSOR_GET_RATE __DIOF(_DCMD_MISC, 0x42, uint32_t)

#endif
