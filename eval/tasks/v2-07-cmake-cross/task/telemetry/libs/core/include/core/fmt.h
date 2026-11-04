/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CORE_FMT_H
#define CORE_FMT_H

#include <stddef.h>
#include <stdio.h>

void fmt_hexdump(FILE *out, const void *data, size_t len);

#endif
