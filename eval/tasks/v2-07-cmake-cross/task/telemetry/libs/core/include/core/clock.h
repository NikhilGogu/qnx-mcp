/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CORE_CLOCK_H
#define CORE_CLOCK_H

#include <stdint.h>

long clock_uptime_s(void);
uint64_t clock_ms(void);
void clock_sleep_ms(unsigned ms);

#endif
