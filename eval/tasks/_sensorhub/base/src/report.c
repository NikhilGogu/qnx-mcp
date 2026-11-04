/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include "alarms.h"
#include "expr.h"
#include "log.h"
#include "registry.h"
#include "report.h"
#include "stats.h"
#include "units.h"

static int by_order(const void *a, const void *b)
{
    const struct sensor *x = *(struct sensor *const *)a, *y = *(struct sensor *const *)b;

    if (x->bus != y->bus)
        return x->bus - y->bus;
    return x->priority != y->priority ? x->priority - y->priority : x->id - y->id;
}

static void report_line(FILE *out, const struct sensor *s)
{
    const struct unit_info *u = s->unit;

    fprintf(out, "%-12s %-4s last %10.3f %-4s mean %10.3f min %10.3f max %10.3f n %ld fail %d\n", s->name, bus_name(s->bus),
            unit_convert(u, s->st.last), u->name, unit_convert(u, stats_mean(&s->st)), unit_convert(u, s->st.min),
            unit_convert(u, s->st.max), s->st.n, s->failures);
}

void report_print(FILE *out, int cycles, int rejected, int disabled)
{
    struct sensor *order[MAX_SENSORS];
    int n = registry_count();

    for (int i = 0; i < n; i++)
        order[i] = registry_get(i);
    qsort(order, (size_t)n, sizeof order[0], by_order);
    fprintf(out, "sensorhub: %d sensors, %d derived, %d rejected, %d disabled, %d cycles\n", n, derive_count(), rejected,
            disabled, cycles);
    for (int i = 0; i < n; i++)
        report_line(out, order[i]);
    for (int i = 0; i < derive_count(); i++) {
        const struct derived *d = derive_get(i);

        if (d->name[0] != '_')
            fprintf(out, "derived %-12s %12.3f\n", d->name, d->valid ? d->value : 0.0);
    }
    alarms_report(out);
    log_msg(LOG_INFO, "report: %d sensors, %d derived, %d rejected", n, derive_count(), rejected);
}
