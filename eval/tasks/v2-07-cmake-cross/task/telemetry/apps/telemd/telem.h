/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef TELEM_H
#define TELEM_H

#include <stdint.h>
#include <endian.h>

#define TELEM_MAGIC 0x544c4d31u

struct telem_pkt {
    uint32_t magic;
    uint32_t seq;
    uint32_t uptime_s;
    int32_t value_milli;
    uint32_t crc;
};

double sampler_next(int seq);

#endif
