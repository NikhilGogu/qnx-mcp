/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "core/fmt.h"

void fmt_hexdump(FILE *out, const void *data, size_t len)
{
    const unsigned char *p = data;

    for (size_t i = 0; i < len; i += 16) {
        fprintf(out, "%04zx ", i);
        for (size_t j = i; j < i + 16 && j < len; j++)
            fprintf(out, " %02x", p[j]);
        fputc('\n', out);
    }
}
