/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef SENSORHUB_H
#define SENSORHUB_H

#include <stddef.h>

#define MAX_SENSORS 64
#define MAX_DERIVED 16
#define MAX_WINDOW  32
#define MAX_DECIM   16
#define NAME_LEN    32
#define NBUSES      4

enum sensor_type { ST_TEMP, ST_PRESS, ST_HUMID, ST_ACCEL, ST_VOLT, ST_RATIO, ST_NTYPES };
enum filter_kind { F_NONE, F_AVG, F_MEDIAN, F_IIR, F_DECIM };
enum bus_id { BUS_I2C0, BUS_I2C1, BUS_SPI0, BUS_ADC0 };

struct unit_info {
    const char *name;
    int quantity;
    double scale;
    double offset;
};

struct stats {
    long n;
    double sum;
    double min;
    double max;
    double last;
};

struct filter_state {
    int kind;
    int window;
    int count;
    int pos;
    double alpha;
    double acc;
    double buf[MAX_WINDOW];
};

struct sensor;

struct driver_ops {
    const char *name;
    int type;
    int (*init)(struct sensor *s);
    int (*read)(struct sensor *s, int cycle, double *value);
};

struct sensor_cfg {
    char name[NAME_LEN];
    char file[64];
    int type;
    int bus;
    int address;
    int channel;
    int priority;
    int enabled;
    int filter;
    int window;
    double window_s;
    double alpha;
    double scale;
    double offset;
    char unit[16];
    char ref[NAME_LEN];
    char desc[96];
};

struct sensor {
    int id;
    char name[NAME_LEN];
    int type;
    int bus;
    int address;
    int channel;
    int priority;
    double scale;
    double offset;
    const struct driver_ops *ops;
    struct sensor *ref;
    int last_raw;
    int failures;
    struct filter_state filt;
    const struct unit_info *unit;
    struct stats st;
    char desc[96];
};

const char *type_name(int type);
int type_from_name(const char *s);
const char *bus_name(int bus);
int bus_from_name(const char *s);

#endif
