/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "core/crc.h"

uint32_t crc32_ieee(const void *data, size_t len)
{
    static uint32_t table[256];
    static int ready;
    const unsigned char *p = data;
    uint32_t c = 0xffffffffu;

    if (!ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t k = i;

            for (int j = 0; j < 8; j++)
                k = k & 1 ? 0xedb88320u ^ (k >> 1) : k >> 1;
            table[i] = k;
        }
        ready = 1;
    }
    while (len--)
        c = table[(c ^ *p++) & 0xff] ^ (c >> 8);
    return c ^ 0xffffffffu;
}
