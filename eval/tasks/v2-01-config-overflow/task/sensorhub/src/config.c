/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"
#include "filter.h"
#include "log.h"
#include "units.h"
#include "util.h"

static const char *const type_names[ST_NTYPES] = { "temp", "press", "humid", "accel", "volt", "ratio" };
static const char *const bus_names[NBUSES] = { "i2c0", "i2c1", "spi0", "adc0" };

const char *type_name(int type)
{
    return type >= 0 && type < ST_NTYPES ? type_names[type] : "?";
}

int type_from_name(const char *s)
{
    for (int i = 0; i < ST_NTYPES; i++)
        if (strcmp(type_names[i], s) == 0)
            return i;
    return -1;
}

const char *bus_name(int bus)
{
    return bus >= 0 && bus < NBUSES ? bus_names[bus] : "?";
}

int bus_from_name(const char *s)
{
    for (int i = 0; i < NBUSES; i++)
        if (strcmp(bus_names[i], s) == 0)
            return i;
    return -1;
}

static void set_defaults(struct sensor_cfg *c, const char *file)
{
    memset(c, 0, sizeof *c);
    copy_str(c->file, sizeof c->file, file);
    c->type = -1;
    c->bus = -1;
    c->priority = 50;
    c->enabled = 1;
    c->filter = F_NONE;
    c->window = 4;
    c->alpha = 0.5;
    c->scale = 1.0;
}

static int set_key(struct sensor_cfg *c, const char *key, const char *val)
{
    long l;

    if (strcmp(key, "name") == 0) {
        if (strlen(val) == 0 || strlen(val) >= NAME_LEN)
            return -1;
        copy_str(c->name, sizeof c->name, val);
    } else if (strcmp(key, "type") == 0) {
        if ((c->type = type_from_name(val)) < 0)
            return -1;
    } else if (strcmp(key, "bus") == 0) {
        if ((c->bus = bus_from_name(val)) < 0)
            return -1;
    } else if (strcmp(key, "address") == 0) {
        if (parse_long(val, 0, 127, &l))
            return -1;
        c->address = (int)l;
    } else if (strcmp(key, "channel") == 0) {
        if (parse_long(val, 0, 15, &l))
            return -1;
        c->channel = (int)l;
    } else if (strcmp(key, "priority") == 0) {
        if (parse_long(val, 0, 999, &l))
            return -1;
        c->priority = (int)l;
    } else if (strcmp(key, "enabled") == 0) {
        if (parse_long(val, 0, 1, &l))
            return -1;
        c->enabled = (int)l;
    } else if (strcmp(key, "filter") == 0) {
        if ((c->filter = filter_from_name(val)) < 0)
            return -1;
    } else if (strcmp(key, "window") == 0) {
        size_t n = strlen(val);

        if (n > 1 && val[n - 1] == 's') {
            char num[32];

            if (n >= sizeof num)
                return -1;
            memcpy(num, val, n - 1);
            num[n - 1] = '\0';
            if (parse_double(num, &c->window_s) || c->window_s <= 0.0)
                return -1;
        } else {
            if (parse_long(val, 0, 1000, &l))
                return -1;
            c->window = (int)l;
            c->window_s = 0.0;
        }
    } else if (strcmp(key, "alpha") == 0) {
        if (parse_double(val, &c->alpha))
            return -1;
    } else if (strcmp(key, "scale") == 0) {
        if (parse_double(val, &c->scale))
            return -1;
    } else if (strcmp(key, "offset") == 0) {
        if (parse_double(val, &c->offset))
            return -1;
    } else if (strcmp(key, "unit") == 0) {
        if (strlen(val) >= sizeof c->unit)
            return -1;
        copy_str(c->unit, sizeof c->unit, val);
    } else if (strcmp(key, "ref") == 0) {
        if (strlen(val) >= NAME_LEN)
            return -1;
        copy_str(c->ref, sizeof c->ref, val);
    } else if (strcmp(key, "desc") == 0) {
        copy_str(c->desc, sizeof c->desc, val);
    } else {
        return -2;
    }
    return 0;
}

static const char *validate(const struct sensor_cfg *c)
{
    static char why[128];
    const struct unit_info *u;

    if (c->name[0] == '\0')
        return "no name";
    if (c->type < 0)
        return "no type";
    if (c->bus < 0)
        return "no bus";
    if ((c->bus == BUS_SPI0 && c->address > 3) || (c->bus == BUS_ADC0 && c->address > 7))
        return "address out of range for the bus";
    if (c->type == ST_RATIO && c->bus != BUS_ADC0)
        return "ratio sensors must be on adc0";
    if (c->type == ST_RATIO && c->ref[0] == '\0')
        return "ratio sensor without ref";
    if (c->type != ST_ACCEL && c->channel != 0)
        return "channel is only valid for accel sensors";
    u = c->unit[0] ? unit_find(c->unit) : unit_default(c->type);
    if (u == NULL || u->quantity != c->type) {
        snprintf(why, sizeof why, "unit '%s' does not fit type %s", c->unit, type_name(c->type));
        return why;
    }
    if ((c->filter == F_AVG || c->filter == F_MEDIAN) && (c->window < 1 || c->window > MAX_WINDOW)) {
        snprintf(why, sizeof why, "window %d out of range 1..%d", c->window, MAX_WINDOW);
        return why;
    }
    if (c->filter == F_DECIM && (c->window < 1 || c->window > MAX_DECIM)) {
        snprintf(why, sizeof why, "decimation %d out of range 1..%d", c->window, MAX_DECIM);
        return why;
    }
    if (c->filter == F_IIR && !(c->alpha > 0.0 && c->alpha <= 1.0))
        return "alpha out of range (0, 1]";
    return NULL;
}

static int parse_file(const char *dir, const char *file, struct sensor_cfg *c)
{
    char path[512], line[256];
    int lineno = 0, bad = 0;
    FILE *f;

    snprintf(path, sizeof path, "%s/%s", dir, file);
    f = fopen(path, "r");
    if (f == NULL) {
        log_msg(LOG_WARN, "%s: cannot open", path);
        return -1;
    }
    set_defaults(c, file);
    while (fgets(line, sizeof line, f)) {
        char *key, *val;
        int r;

        lineno++;
        r = split_kv(line, &key, &val);
        if (r == 0)
            continue;
        if (r < 0 || (r = set_key(c, key, val)) == -1) {
            log_msg(LOG_WARN, "%s:%d: bad line", file, lineno);
            bad = 1;
        } else if (r == -2) {
            log_msg(LOG_INFO, "%s:%d: unknown key '%s' ignored", file, lineno, key);
        }
    }
    fclose(f);
    return bad ? -1 : 0;
}

static double bus_rate[NBUSES] = { 10.0, 10.0, 10.0, 10.0 };

/* buses.conf: "<bus> rate_hz=<n>" per line; buses not listed sample at 10 Hz. */
static void load_bus_rates(const char *dir)
{
    char path[512], line[128], name[16];
    double hz;
    FILE *f;

    snprintf(path, sizeof path, "%s/buses.conf", dir);
    f = fopen(path, "r");
    if (f == NULL)
        return;
    while (fgets(line, sizeof line, f)) {
        int b;

        if (sscanf(line, " %15s rate_hz = %lf", name, &hz) != 2 && sscanf(line, " %15s rate_hz=%lf", name, &hz) != 2)
            continue;
        b = bus_from_name(name);
        if (b >= 0 && hz > 0.0 && hz <= 1000.0)
            bus_rate[b] = hz;
        else
            log_msg(LOG_WARN, "buses.conf: bad line for %s ignored", name);
    }
    fclose(f);
}

static void convert_window(struct sensor_cfg *c)
{
    if (c->window_s > 0.0 && c->bus >= 0)
        c->window = (int)(c->window_s * bus_rate[c->bus] + 0.5);
}

static int cmp_names(const void *a, const void *b)
{
    return strcmp(*(char *const *)a, *(char *const *)b);
}

int config_load(const char *dir, struct sensor_cfg *out, int max, struct config_result *res)
{
    char *names[256];
    int nnames = 0;
    struct dirent *de;
    DIR *d = opendir(dir);

    memset(res, 0, sizeof *res);
    if (d == NULL)
        return -1;
    load_bus_rates(dir);
    while ((de = readdir(d)) != NULL && nnames < 256) {
        if (!ends_with(de->d_name, ".cfg") || strcmp(de->d_name, "derived.cfg") == 0)
            continue;
        names[nnames++] = strdup(de->d_name);
    }
    closedir(d);
    qsort(names, (size_t)nnames, sizeof names[0], cmp_names);
    for (int i = 0; i < nnames; i++) {
        struct sensor_cfg *c = &out[res->loaded];
        const char *why = NULL;

        if (res->loaded >= max) {
            log_msg(LOG_WARN, "%s: more than %d sensors, ignored", names[i], max);
            res->rejected++;
        } else if (parse_file(dir, names[i], c) != 0) {
            log_msg(LOG_WARN, "%s: rejected: parse error", names[i]);
            res->rejected++;
        } else if (!c->enabled) {
            log_msg(LOG_INFO, "%s: sensor %s disabled", names[i], c->name);
            res->disabled++;
        } else if ((why = validate(c)) != NULL) {
            log_msg(LOG_WARN, "%s: sensor %s rejected: %s", names[i], c->name, why);
            res->rejected++;
        } else {
            convert_window(c);
            res->loaded++;
        }
        free(names[i]);
    }
    return res->loaded;
}
