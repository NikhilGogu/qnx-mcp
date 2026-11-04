/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include "parsers.h"

static char *trim(char *s)
{
    char *e;

    while (*s == ' ' || *s == '\t')
        s++;
    e = s + strlen(s);
    while (e > s && (e[-1] == '\n' || e[-1] == '\r' || e[-1] == ' ' || e[-1] == '\t'))
        *--e = '\0';
    return s;
}

static int apply(struct comp_state *cs, const char *key, const char *val, int *have_state)
{
    char *end;

    if (strcmp(key, "state") == 0) {
        if (strlen(val) == 0 || strlen(val) >= sizeof cs->state)
            return -1;
        strcpy(cs->state, val);
        *have_state = 1;
    } else if (strcmp(key, "since") == 0) {
        cs->since = strtol(val, &end, 10);
        if (*end != '\0')
            return -1;
    } else if (strcmp(key, "code") == 0) {
        cs->code = (int)strtol(val, &end, 10);
        if (*end != '\0')
            return -1;
    }
    return 0;
}

int detect_format(const char *path, int *format)
{
    char line[128];
    FILE *f = fopen(path, "r");

    if (f == NULL)
        return errno;
    if (fgets(line, sizeof line, f) == NULL)
        line[0] = '\0';
    fclose(f);
    if (strncmp(line, "#v2", 3) == 0)
        *format = FMT_SECTIONS;
    else if (strncmp(line, "STATE ", 6) == 0)
        *format = FMT_LEGACY;
    else
        *format = FMT_KV;
    return 0;
}

/* key=value per line */
int parse_kv(const char *path, struct comp_state *cs)
{
    char line[128];
    int have_state = 0;
    FILE *f = fopen(path, "r");

    if (f == NULL)
        return errno;
    memset(cs, 0, sizeof *cs);
    while (fgets(line, sizeof line, f)) {
        char *eq = strchr(line, '=');

        if (line[0] == '#' || eq == NULL)
            continue;
        *eq = '\0';
        if (apply(cs, trim(line), trim(eq + 1), &have_state) != 0) {
            fclose(f);
            return EBADMSG;
        }
    }
    fclose(f);
    return have_state ? 0 : EBADMSG;
}

/* "#v2" header, then [status] section with "key = value" lines */
int parse_sections(const char *path, struct comp_state *cs)
{
    char line[128];
    int have_state = 0, in_status = 0;
    FILE *f = fopen(path, "r");

    if (f == NULL)
        return errno;
    memset(cs, 0, sizeof *cs);
    while (fgets(line, sizeof line, f)) {
        char *t = trim(line), *eq;

        if (*t == '#' || *t == '\0')
            continue;
        if (*t == '[') {
            in_status = strcmp(t, "[status]") == 0;
            continue;
        }
        eq = strchr(t, '=');
        if (!in_status || eq == NULL)
            continue;
        *eq = '\0';
        if (apply(cs, trim(t), trim(eq + 1), &have_state) != 0) {
            fclose(f);
            return EBADMSG;
        }
    }
    fclose(f);
    return have_state ? 0 : EBADMSG;
}

/* single line: STATE <state> <since> <code> */
int parse_legacy(const char *path, struct comp_state *cs)
{
    char line[128];
    FILE *f = fopen(path, "r");
    int ok;

    if (f == NULL)
        return errno;
    memset(cs, 0, sizeof *cs);
    ok = fgets(line, sizeof line, f) != NULL && sscanf(line, "STATE %23s %ld %d", cs->state, &cs->since, &cs->code) == 3;
    fclose(f);
    return ok ? 0 : EBADMSG;
}
