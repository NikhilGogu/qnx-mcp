/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "calib.h"

static int parse_line(char *line, struct sensor *s)
{
    char *tok = strtok(line, " \t\n");

    if (!tok || tok[0] == '#')
        return 0;
    memset(s, 0, sizeof *s);
    snprintf(s->name, sizeof s->name, "%s", tok);
    while ((tok = strtok(NULL, " \t\n")) && s->nsamples < 16)
        s->samples[s->nsamples++] = atoi(tok);
    s->cal = calib_find(s->name);
    return 1;
}

int main(int argc, char **argv)
{
    const char *path = argc > 1 ? argv[1] : "sensors.cfg";
    FILE *f = fopen(path, "r");
    char line[256];
    struct sensor s;
    int count = 0;

    if (!f) {
        perror(path);
        return 1;
    }
    while (fgets(line, sizeof line, f)) {
        if (!parse_line(line, &s))
            continue;
        printf("%-8s %9.2f%s\n", s.name, sensor_average(&s), s.cal ? "" : "  (uncalibrated)");
        count++;
    }
    fclose(f);
    printf("%d sensors\n", count);
    return 0;
}
