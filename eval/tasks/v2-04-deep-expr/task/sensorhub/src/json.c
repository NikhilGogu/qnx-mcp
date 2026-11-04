/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include "expr.h"
#include "json.h"
#include "registry.h"
#include "stats.h"
#include "units.h"

static void json_string(FILE *out, const char *s)
{
    fputc('"', out);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\')
            fprintf(out, "\\%c", *s);
        else if ((unsigned char)*s < 0x20)
            fprintf(out, "\\u%04x", (unsigned char)*s);
        else
            fputc(*s, out);
    }
    fputc('"', out);
}

void json_report(FILE *out, int cycles, int rejected, int disabled)
{
    fprintf(out, "{\"cycles\": %d, \"rejected\": %d, \"disabled\": %d, \"sensors\": [", cycles, rejected, disabled);
    for (int i = 0; i < registry_count(); i++) {
        const struct sensor *s = registry_get(i);
        const struct unit_info *u = s->unit;

        fprintf(out, "%s\n  {\"name\": ", i ? "," : "");
        json_string(out, s->name);
        fprintf(out, ", \"bus\": \"%s\", \"type\": \"%s\", \"unit\": ", bus_name(s->bus), type_name(s->type));
        json_string(out, u->name);
        fprintf(out, ", \"last\": %.6f, \"mean\": %.6f, \"min\": %.6f, \"max\": %.6f, \"n\": %ld, \"failures\": %d}",
                unit_convert(u, s->st.last), unit_convert(u, stats_mean(&s->st)), unit_convert(u, s->st.min),
                unit_convert(u, s->st.max), s->st.n, s->failures);
    }
    fprintf(out, "\n], \"derived\": [");
    for (int i = 0; i < derive_count(); i++) {
        const struct derived *d = derive_get(i);

        fprintf(out, "%s\n  {\"name\": ", i ? "," : "");
        json_string(out, d->name);
        fprintf(out, ", \"value\": %.6f}", d->valid ? d->value : 0.0);
    }
    fprintf(out, "\n]}\n");
}
