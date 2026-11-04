/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include <string.h>
#include "drivers.h"
#include "filter.h"
#include "log.h"
#include "registry.h"
#include "stats.h"
#include "units.h"
#include "util.h"

static struct sensor sensors[MAX_SENSORS];
static int nsensors;

int registry_count(void)
{
    return nsensors;
}

struct sensor *registry_get(int i)
{
    return i >= 0 && i < nsensors ? &sensors[i] : NULL;
}

struct sensor *registry_find(const char *name)
{
    for (int i = 0; i < nsensors; i++)
        if (strcmp(sensors[i].name, name) == 0)
            return &sensors[i];
    return NULL;
}

static void remove_at(int i)
{
    memmove(&sensors[i], &sensors[i + 1], (size_t)(nsensors - i - 1) * sizeof sensors[0]);
    nsensors--;
}

static int bind_ref(struct sensor *s, const struct sensor_cfg *c)
{
    struct sensor *r;

    if (s->type != ST_RATIO)
        return 0;
    r = registry_find(c->ref);
    if (r == NULL || r == s || r->bus != s->bus) {
        log_msg(LOG_WARN, "%s: sensor %s rejected: ref '%s' is not an enabled sensor on %s", c->file, s->name, c->ref,
                bus_name(s->bus));
        return -1;
    }
    s->ref = r;
    return 0;
}

int registry_build(const struct sensor_cfg *cfgs, int n, int *rejected)
{
    const struct sensor_cfg *owner[MAX_SENSORS];

    nsensors = 0;
    for (int i = 0; i < n; i++) {
        const struct sensor_cfg *c = &cfgs[i];
        struct sensor *s = &sensors[nsensors];

        if (registry_find(c->name)) {
            log_msg(LOG_WARN, "%s: sensor %s rejected: duplicate name", c->file, c->name);
            (*rejected)++;
            continue;
        }
        memset(s, 0, sizeof *s);
        s->id = nsensors;
        copy_str(s->name, sizeof s->name, c->name);
        copy_str(s->desc, sizeof s->desc, c->desc);
        s->type = c->type;
        s->bus = c->bus;
        s->address = c->address;
        s->channel = c->channel;
        s->priority = c->priority;
        s->scale = c->scale;
        s->offset = c->offset;
        s->ops = driver_for(c->type);
        s->unit = c->unit[0] ? unit_find(c->unit) : unit_default(c->type);
        filter_init(&s->filt, c->filter, c->window, c->alpha);
        stats_reset(&s->st);
        if (s->ops->init(s) != 0) {
            log_msg(LOG_WARN, "%s: sensor %s rejected by the %s driver", c->file, s->name, s->ops->name);
            (*rejected)++;
            continue;
        }
        owner[nsensors] = c;
        nsensors++;
    }
    for (int i = 0; i < nsensors;) {
        if (bind_ref(&sensors[i], owner[i]) != 0) {
            memmove(&owner[i], &owner[i + 1], (size_t)(nsensors - i - 1) * sizeof owner[0]);
            remove_at(i);
            (*rejected)++;
            i = 0;
            continue;
        }
        i++;
    }
    for (int i = 0; i < nsensors; i++) {
        sensors[i].id = i;
        if (sensors[i].type == ST_RATIO)
            sensors[i].ref = registry_find(owner[i]->ref);
    }
    return nsensors;
}
