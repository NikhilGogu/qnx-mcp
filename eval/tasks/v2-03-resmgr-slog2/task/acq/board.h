/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef BOARD_H
#define BOARD_H

#include "acq.h"

int board_load(const char *path, struct acq_limits *lim, int *rev);

#endif
