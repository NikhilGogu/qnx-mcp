/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include "util.h"

char *str_trim(char *s)
{
    char *end;

    while (isspace((unsigned char)*s))
        s++;
    if (*s == '\0')
        return s;
    end = s + strlen(s) - 1;
    while (end > s && isspace((unsigned char)*end))
        *end-- = '\0';
    return s;
}

int split_kv(char *line, char **key, char **val)
{
    char *hash = strchr(line, '#');
    char *eq;

    if (hash)
        *hash = '\0';
    line = str_trim(line);
    if (*line == '\0')
        return 0;
    eq = strchr(line, '=');
    if (eq == NULL)
        return -1;
    *eq = '\0';
    *key = str_trim(line);
    *val = str_trim(eq + 1);
    return **key ? 1 : -1;
}

int parse_long(const char *s, long lo, long hi, long *out)
{
    char *end;
    long v;

    errno = 0;
    v = strtol(s, &end, 0);
    if (errno || end == s || *end != '\0' || v < lo || v > hi)
        return -1;
    *out = v;
    return 0;
}

int parse_double(const char *s, double *out)
{
    char *end;
    double v;

    errno = 0;
    v = strtod(s, &end);
    if (errno || end == s || *end != '\0')
        return -1;
    *out = v;
    return 0;
}

void copy_str(char *dst, size_t n, const char *src)
{
    size_t len = strlen(src);

    if (len >= n)
        len = n - 1;
    memcpy(dst, src, len);
    dst[len] = '\0';
}

int ends_with(const char *s, const char *suffix)
{
    size_t a = strlen(s), b = strlen(suffix);

    return a >= b && strcmp(s + a - b, suffix) == 0;
}
