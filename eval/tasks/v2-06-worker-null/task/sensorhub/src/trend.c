/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <pthread.h>
#include <string.h>
#include "registry.h"
#include "trend.h"
#include "units.h"

/* Least-squares slope over the last TREND_LEN filtered values of each
 * sensor, reported per cycle in the configured unit (printed with -v). */
struct trend {
    double v[TREND_LEN];
    int n;
    int pos;
};

static struct trend trends[MAX_SENSORS];
static pthread_mutex_t trend_lock = PTHREAD_MUTEX_INITIALIZER;

void trend_reset(void)
{
    memset(trends, 0, sizeof trends);
}

void trend_add(const struct sensor *s, double value)
{
    struct trend *t = &trends[s->id];

    pthread_mutex_lock(&trend_lock);
    t->v[t->pos] = value;
    t->pos = (t->pos + 1) % TREND_LEN;
    if (t->n < TREND_LEN)
        t->n++;
    pthread_mutex_unlock(&trend_lock);
}

static double slope(const struct trend *t)
{
    double sx = 0, sy = 0, sxx = 0, sxy = 0;
    int start = t->n < TREND_LEN ? 0 : t->pos;

    if (t->n < 2)
        return 0.0;
    for (int i = 0; i < t->n; i++) {
        double y = t->v[(start + i) % TREND_LEN];

        sx += i;
        sy += y;
        sxx += (double)i * i;
        sxy += i * y;
    }
    return (t->n * sxy - sx * sy) / (t->n * sxx - sx * sx);
}

void trend_report(FILE *out)
{
    for (int i = 0; i < registry_count(); i++) {
        const struct sensor *s = registry_get(i);

        fprintf(out, "trend %-12s %+9.4f %s/cycle\n", s->name, slope(&trends[i]) * s->unit->scale, s->unit->name);
    }
}
