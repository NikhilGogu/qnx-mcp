/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef PARSERS_H
#define PARSERS_H

#include <stdio.h>

struct comp_state {
    char state[24];
    long since;
    int code;
};

/* Each returns 0, or an errno value: ENOENT, EBADMSG (malformed file) or
 * whatever opening or reading the file failed with. */
int parse_kv(const char *path, struct comp_state *cs);
int parse_sections(const char *path, struct comp_state *cs);
int parse_legacy(const char *path, struct comp_state *cs);
int detect_format(const char *path, int *format);

enum { FMT_KV, FMT_SECTIONS, FMT_LEGACY };

#endif
