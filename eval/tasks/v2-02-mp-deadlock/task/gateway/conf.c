/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "conf.h"

static char *trim(char *s)
{
    char *e;

    while (isspace((unsigned char)*s))
        s++;
    e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1]))
        *--e = '\0';
    return s;
}

int conf_get(const char *path, const char *key, char *out, int outlen)
{
    char line[256];
    FILE *f = fopen(path, "r");
    int found = 0;

    if (f == NULL)
        return -1;
    while (!found && fgets(line, sizeof line, f)) {
        char *hash = strchr(line, '#'), *eq, *k;

        if (hash)
            *hash = '\0';
        eq = strchr(line, '=');
        if (eq == NULL)
            continue;
        *eq = '\0';
        k = trim(line);
        if (strcmp(k, key) == 0) {
            snprintf(out, (size_t)outlen, "%s", trim(eq + 1));
            found = 1;
        }
    }
    fclose(f);
    return found ? 0 : -1;
}

long conf_get_long(const char *path, const char *key, long dflt)
{
    char buf[64];

    return conf_get(path, key, buf, sizeof buf) == 0 ? strtol(buf, NULL, 0) : dflt;
}
