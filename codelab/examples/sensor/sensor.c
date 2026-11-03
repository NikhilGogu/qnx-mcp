/*
 * sensor: turn raw readings from an IMU and a barometer into calibrated
 * values, and log implausible readings to slog2.
 *
 *   sensor FILE         read FILE once and print the average of each channel
 *   sensor -f FILE      replay FILE forever, one reading every 200 ms
 *
 * FILE has one reading per line, "channel,raw", and '#' comment lines.
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/slog2.h>

struct calibration {
    const char *channel;
    double scale;  /* units per raw count */
    double offset; /* raw count at zero */
    double limit;  /* readings beyond +/- limit are implausible */
    const char *unit;
};

static const struct calibration calibrations[] = {
    {"accel_x", 0.00981, 12.0, 20.0, "m/s2"},
    {"accel_y", 0.00981, -7.0, 20.0, "m/s2"},
    {"accel_z", 0.00981, 3.0, 20.0, "m/s2"},
    {"gyro_z", 0.0175, 0.0, 8.7, "rad/s"},
    {"baro", 0.01, 0.0, 1100.0, "hPa"},
};
#define NCHANNELS (sizeof calibrations / sizeof calibrations[0])

struct totals {
    double sum[NCHANNELS];
    int count[NCHANNELS];
    int readings;
    int lines;
};

static slog2_buffer_t logbuf;

static void log_init(void)
{
    slog2_buffer_set_config_t config = {
        .num_buffers = 1,
        .buffer_set_name = "sensor",
        .verbosity_level = SLOG2_INFO,
        .buffer_config = {{.buffer_name = "readings", .num_pages = 4}},
    };
    if (slog2_register(&config, &logbuf, 0) == -1) {
        fprintf(stderr, "sensor: slog2_register failed, logging to stderr only\n");
        logbuf = NULL;
    }
}

static int by_channel(const void *key, const void *entry)
{
    return strcmp(key, ((const struct calibration *)entry)->channel);
}

static const struct calibration *find_calibration(const char *channel)
{
    return bsearch(channel, calibrations, NCHANNELS, sizeof calibrations[0], by_channel);
}

static double calibrate(const struct calibration *cal, int raw)
{
    return cal->scale * (raw - cal->offset);
}

/* Returns the calibration used, or NULL for a line that holds no reading. */
static const struct calibration *process_line(const char *line, int line_no, double *value)
{
    char channel[32];
    int raw;

    if (line[0] == '#' || sscanf(line, "%31[^,],%d", channel, &raw) != 2) {
        return NULL;
    }
    const struct calibration *cal = find_calibration(channel);
    *value = calibrate(cal, raw);
    if (*value > cal->limit || *value < -cal->limit) {
        slog2f(logbuf, 0, SLOG2_WARNING, "line %d: %s reading %.2f %s is outside +/-%.2f", line_no, cal->channel, *value,
               cal->unit, cal->limit);
    }
    return cal;
}

static int read_file(const char *path, struct totals *t, int follow_ms)
{
    FILE *f = fopen(path, "r");
    if (f == NULL) {
        perror(path);
        return -1;
    }
    char line[128];
    int line_no = 0;
    while (fgets(line, sizeof line, f) != NULL) {
        double value;
        line_no++;
        t->lines++;
        const struct calibration *cal = process_line(line, line_no, &value);
        if (cal == NULL) {
            continue;
        }
        size_t i = (size_t)(cal - calibrations);
        t->sum[i] += value;
        t->count[i]++;
        t->readings++;
        if (follow_ms > 0) {
            printf("%-8s %9.2f %s\n", cal->channel, value, cal->unit);
            fflush(stdout);
            usleep((useconds_t)follow_ms * 1000);
        }
    }
    fclose(f);
    return 0;
}

int main(int argc, char *argv[])
{
    int follow_ms = 0;
    int opt;
    while ((opt = getopt(argc, argv, "f")) != -1) {
        if (opt == 'f') {
            follow_ms = 200;
        } else {
            fprintf(stderr, "usage: sensor [-f] FILE\n");
            return 2;
        }
    }
    if (optind != argc - 1) {
        fprintf(stderr, "usage: sensor [-f] FILE\n");
        return 2;
    }
    const char *path = argv[optind];
    log_init();
    slog2f(logbuf, 0, SLOG2_INFO, "reading %s%s", path, follow_ms ? " in follow mode" : "");

    struct totals t = {0};
    do {
        if (read_file(path, &t, follow_ms) != 0) {
            return 1;
        }
    } while (follow_ms > 0);

    printf("%-8s %8s %9s  %s\n", "channel", "readings", "average", "unit");
    for (size_t i = 0; i < NCHANNELS; i++) {
        double avg = t.count[i] ? t.sum[i] / t.count[i] : 0.0;
        printf("%-8s %8d %9.2f  %s\n", calibrations[i].channel, t.count[i], avg, calibrations[i].unit);
    }
    printf("%d readings from %d lines\n", t.readings, t.lines);
    slog2f(logbuf, 0, SLOG2_INFO, "%d readings from %d lines", t.readings, t.lines);
    return 0;
}
