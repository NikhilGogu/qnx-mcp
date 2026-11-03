/*
 * crash_demo: a small sensor-averaging program with a deliberate bug.
 * The "lidar" sensor has no calibration table, and read_calibration()
 * dereferences that NULL pointer two calls below main().
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct sensor {
    const char *name;
    const int *calibration; /* NULL when the sensor was never calibrated */
    int samples;
};

static int read_calibration(const struct sensor *s, int index)
{
    return s->calibration[index]; /* CRASH-LINE */
}

static int average_offset(const struct sensor *s)
{
    int sum = 0;
    for (int i = 0; i < s->samples; i++) {
        sum += read_calibration(s, i);
    }
    return s->samples ? sum / s->samples : 0;
}

int main(int argc, char *argv[])
{
    static const int imu_table[4] = {3, 5, 7, 9};
    struct sensor sensors[] = {
        {"imu", imu_table, 4},
        {"lidar", NULL, 4},
    };
    int only = argc > 1 ? atoi(argv[1]) : -1;

    for (size_t i = 0; i < sizeof sensors / sizeof sensors[0]; i++) {
        if (only >= 0 && (size_t)only != i) {
            continue;
        }
        printf("%s: average offset %d\n", sensors[i].name, average_offset(&sensors[i]));
        fflush(stdout);
    }
    return 0;
}
