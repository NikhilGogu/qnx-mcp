/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "drivers.h"
#include "log.h"
#include "sim.h"

static const struct {
    int zero;
    double gain;
} axis[3] = { { 2048, 0.0040 }, { 2040, 0.0041 }, { 2056, 0.0039 } };

static int sample(struct sensor *s, int cycle)
{
    int raw = sim_raw(s->bus, s->address, s->channel, cycle);

    s->last_raw = raw;
    return raw;
}

static int no_init(struct sensor *s)
{
    (void)s;
    return 0;
}

static int temp_read(struct sensor *s, int cycle, double *v)
{
    *v = sample(s, cycle) * 0.0625 - 40.0;
    return 0;
}

static int press_read(struct sensor *s, int cycle, double *v)
{
    *v = sample(s, cycle) / 4.0 + 300.0;
    return 0;
}

static int humid_read(struct sensor *s, int cycle, double *v)
{
    double h = sample(s, cycle) * 100.0 / 4095.0;

    *v = h > 100.0 ? 100.0 : h;
    return 0;
}

static int accel_init(struct sensor *s)
{
    if (s->channel < 0 || s->channel > 2) {
        log_msg(LOG_WARN, "sensor %s: accel channel %d is not an axis (0..2)", s->name, s->channel);
        return -1;
    }
    return 0;
}

static int accel_read(struct sensor *s, int cycle, double *v)
{
    *v = (sample(s, cycle) - axis[s->channel].zero) * axis[s->channel].gain;
    return 0;
}

static int volt_read(struct sensor *s, int cycle, double *v)
{
    *v = sample(s, cycle) * 0.805;
    return 0;
}

static int ratio_read(struct sensor *s, int cycle, double *v)
{
    int raw = sample(s, cycle);

    if (s->ref->last_raw == 0)
        return -1;
    *v = 100.0 * raw / s->ref->last_raw;
    return 0;
}

static const struct driver_ops ops[ST_NTYPES] = {
    { "temp", ST_TEMP, no_init, temp_read },
    { "press", ST_PRESS, no_init, press_read },
    { "humid", ST_HUMID, no_init, humid_read },
    { "accel", ST_ACCEL, accel_init, accel_read },
    { "volt", ST_VOLT, no_init, volt_read },
    { "ratio", ST_RATIO, no_init, ratio_read },
};

const struct driver_ops *driver_for(int type)
{
    return type >= 0 && type < ST_NTYPES ? &ops[type] : NULL;
}
