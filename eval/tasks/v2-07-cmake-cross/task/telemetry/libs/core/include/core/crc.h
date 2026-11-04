/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CORE_CRC_H
#define CORE_CRC_H

#include <stddef.h>
#include <stdint.h>

uint32_t crc32_ieee(const void *data, size_t len);

#endif
