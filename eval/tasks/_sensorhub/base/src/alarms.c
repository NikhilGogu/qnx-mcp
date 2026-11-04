/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include "alarms.h"
#include "log.h"
#include "registry.h"
#include "util.h"

/* alarms.conf: one rule per line, "sensor lo=<v> hi=<v> hyst=<v>", values in
 * the sensor's natural unit. An alarm is raised when the filtered value leaves
 * [lo, hi] and cleared when it is back inside by at least the hysteresis. */
#define MAX_RULES 32

struct alarm_state {
    struct alarm_rule rule;
    const struct sensor *sensor;
    int active;
    int raised;
    int first_cycle;
    int last_cycle;
};

static struct alarm_state rules[MAX_RULES];
static int nrules;
static pthread_mutex_t alarm_lock = PTHREAD_MUTEX_INITIALIZER;

static int parse_rule(char *line, struct alarm_rule *r)
{
    char *save = NULL, *tok = strtok_r(line, " \t", &save);

    memset(r, 0, sizeof *r);
    if (tok == NULL || strlen(tok) >= sizeof r->sensor)
        return -1;
    copy_str(r->sensor, sizeof r->sensor, tok);
    while ((tok = strtok_r(NULL, " \t", &save)) != NULL) {
        char *eq = strchr(tok, '=');
        double v;

        if (eq == NULL)
            return -1;
        *eq = '\0';
        if (parse_double(eq + 1, &v))
            return -1;
        if (strcmp(tok, "lo") == 0) {
            r->lo = v;
            r->has_lo = 1;
        } else if (strcmp(tok, "hi") == 0) {
            r->hi = v;
            r->has_hi = 1;
        } else if (strcmp(tok, "hyst") == 0) {
            if (v < 0)
                return -1;
            r->hysteresis = v;
        } else {
            return -1;
        }
    }
    if (!r->has_lo && !r->has_hi)
        return -1;
    if (r->has_lo && r->has_hi && r->lo >= r->hi)
        return -1;
    return 0;
}

int alarms_load(const char *dir)
{
    char path[512], line[256];
    int lineno = 0;
    FILE *f;

    snprintf(path, sizeof path, "%s/alarms.conf", dir);
    f = fopen(path, "r");
    if (f == NULL)
        return 0;
    while (fgets(line, sizeof line, f)) {
        char *t;

        lineno++;
        t = strchr(line, '#');
        if (t)
            *t = '\0';
        t = str_trim(line);
        if (*t == '\0')
            continue;
        if (nrules == MAX_RULES) {
            log_msg(LOG_WARN, "alarms.conf:%d: too many rules, ignored", lineno);
            continue;
        }
        if (parse_rule(t, &rules[nrules].rule) != 0) {
            log_msg(LOG_WARN, "alarms.conf:%d: bad rule ignored", lineno);
            continue;
        }
        nrules++;
    }
    fclose(f);
    return nrules;
}

void alarms_bind(void)
{
    for (int i = 0; i < nrules;) {
        rules[i].sensor = registry_find(rules[i].rule.sensor);
        if (rules[i].sensor == NULL) {
            log_msg(LOG_WARN, "alarms.conf: no sensor %s, rule dropped", rules[i].rule.sensor);
            memmove(&rules[i], &rules[i + 1], (size_t)(nrules - i - 1) * sizeof rules[0]);
            nrules--;
            continue;
        }
        i++;
    }
}

static int outside(const struct alarm_rule *r, double v, double margin)
{
    return (r->has_lo && v < r->lo + margin) || (r->has_hi && v > r->hi - margin);
}

void alarms_check(const struct sensor *s, double value, int cycle)
{
    pthread_mutex_lock(&alarm_lock);
    for (int i = 0; i < nrules; i++) {
        struct alarm_state *a = &rules[i];

        if (a->sensor != s)
            continue;
        if (!a->active && outside(&a->rule, value, 0.0)) {
            a->active = 1;
            a->raised++;
            if (a->raised == 1)
                a->first_cycle = cycle;
            a->last_cycle = cycle;
            log_msg(LOG_WARN, "alarm: %s = %.3f outside its limits (cycle %d)", s->name, value, cycle);
        } else if (a->active && !outside(&a->rule, value, -a->rule.hysteresis)) {
            a->active = 0;
            log_msg(LOG_INFO, "alarm cleared: %s = %.3f (cycle %d)", s->name, value, cycle);
        }
    }
    pthread_mutex_unlock(&alarm_lock);
}

void alarms_report(FILE *out)
{
    for (int i = 0; i < nrules; i++) {
        const struct alarm_state *a = &rules[i];

        if (a->raised)
            fprintf(out, "alarm %-12s raised %d first cycle %d last cycle %d%s\n", a->rule.sensor, a->raised,
                    a->first_cycle, a->last_cycle, a->active ? " active" : "");
        else
            fprintf(out, "alarm %-12s quiet\n", a->rule.sensor);
    }
}

int alarms_count(void)
{
    return nrules;
}
