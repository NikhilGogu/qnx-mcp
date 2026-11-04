/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef UTIL_H
#define UTIL_H

#include <stddef.h>

char *str_trim(char *s);
int split_kv(char *line, char **key, char **val);
int parse_long(const char *s, long lo, long hi, long *out);
int parse_double(const char *s, double *out);
void copy_str(char *dst, size_t n, const char *src);
int ends_with(const char *s, const char *suffix);

#endif
