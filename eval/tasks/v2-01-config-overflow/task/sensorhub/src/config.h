/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CONFIG_H
#define CONFIG_H

#include "sensorhub.h"

struct config_result {
    int loaded;
    int rejected;
    int disabled;
};

int config_load(const char *dir, struct sensor_cfg *out, int max, struct config_result *res);

#endif
