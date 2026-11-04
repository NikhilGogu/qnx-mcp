/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include "alarms.h"
#include "bus.h"
#include "filter.h"
#include "log.h"
#include "registry.h"
#include "stats.h"
#include "trend.h"

struct bus {
    int id;
    struct sensor *sensors[MAX_SENSORS];
    int n;
    struct worker w;
};

static struct bus buses[NBUSES];

static int by_priority(const void *a, const void *b)
{
    const struct sensor *x = *(struct sensor *const *)a, *y = *(struct sensor *const *)b;

    return x->priority != y->priority ? x->priority - y->priority : x->id - y->id;
}

static int sample_sensor(struct sensor *s, int cycle)
{
    double v, out;

    if (s->ops->read(s, cycle, &v) != 0) {
        s->failures++;
        return -1;
    }
    v = v * s->scale + s->offset;
    if (filter_apply(&s->filt, v, &out)) {
        stats_update(&s->st, out);
        trend_add(s, out);
        alarms_check(s, out, cycle);
    }
    return 0;
}

static int bus_step(void *ctx, int cycle)
{
    struct bus *b = ctx;
    int errors = 0;

    for (int i = 0; i < b->n; i++)
        if (sample_sensor(b->sensors[i], cycle) != 0)
            errors++;
    if (errors)
        log_msg(LOG_DEBUG, "%s: cycle %d: %d read failures", bus_name(b->id), cycle, errors);
    return 0;
}

int bus_sensor_count(int bus)
{
    return buses[bus].n;
}

int bus_setup(struct worker **out)
{
    int nw = 0;

    for (int b = 0; b < NBUSES; b++) {
        buses[b].id = b;
        buses[b].n = 0;
    }
    for (int i = 0; i < registry_count(); i++) {
        struct sensor *s = registry_get(i);
        struct bus *b = &buses[s->bus];

        b->sensors[b->n++] = s;
    }
    for (int b = 0; b < NBUSES; b++) {
        if (buses[b].n == 0)
            continue;
        qsort(buses[b].sensors, (size_t)buses[b].n, sizeof buses[b].sensors[0], by_priority);
        buses[b].w.name = bus_name(b);
        buses[b].w.step = bus_step;
        buses[b].w.ctx = &buses[b];
        if (worker_start(&buses[b].w, 0) != 0)
            return -1;
        out[nw++] = &buses[b].w;
    }
    return nw;
}
