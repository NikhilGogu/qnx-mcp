/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "alarms.h"
#include "bus.h"
#include "config.h"
#include "expr.h"
#include "json.h"
#include "log.h"
#include "registry.h"
#include "report.h"
#include "selftest.h"
#include "trend.h"

int main(int argc, char **argv)
{
    static struct sensor_cfg cfgs[MAX_SENSORS];
    struct config_result res;
    struct worker *bus_workers[NBUSES], *derive_workers[1];
    const char *dir = "/etc/sensor.d";
    int cycles = 50, verbose = 0, json = 0, nbus, nderive, rejected, opt;

    while ((opt = getopt(argc, argv, "c:jn:v")) != -1) {
        switch (opt) {
        case 'c':
            dir = optarg;
            break;
        case 'j':
            json = 1;
            break;
        case 'n':
            cycles = atoi(optarg);
            break;
        case 'v':
            verbose++;
            break;
        default:
            fprintf(stderr, "usage: sensorhub [-c confdir] [-n cycles] [-j] [-v]\n");
            return 2;
        }
    }
    if (cycles < 1)
        cycles = 1;
    log_init("sensorhub", verbose);
    if (config_load(dir, cfgs, MAX_SENSORS, &res) < 0) {
        log_msg(LOG_ERR, "cannot read configuration directory %s", dir);
        return 1;
    }
    rejected = res.rejected;
    registry_build(cfgs, res.loaded, &rejected);
    if (registry_count() == 0) {
        log_msg(LOG_ERR, "no usable sensors in %s", dir);
        return 1;
    }
    if (selftest_run() != 0) {
        log_msg(LOG_ERR, "selftest failed");
        return 1;
    }
    alarms_load(dir);
    alarms_bind();
    trend_reset();
    nbus = bus_setup(bus_workers);
    nderive = derive_setup(dir, derive_workers);
    if (nbus < 0 || nderive < 0)
        return 1;
    log_msg(LOG_INFO, "%d sensors on %d buses, %d derived lines, %d cycles", registry_count(), nbus, nderive, cycles);
    for (int c = 0; c < cycles; c++) {
        worker_run(bus_workers, nbus, c);
        if (nderive)
            worker_run(derive_workers, nderive, c);
    }
    worker_stop_all(bus_workers, nbus);
    if (nderive)
        worker_stop_all(derive_workers, nderive);
    if (json)
        json_report(stdout, cycles, rejected, res.disabled);
    else
        report_print(stdout, cycles, rejected, res.disabled);
    if (verbose)
        trend_report(stderr);
    log_close();
    return 0;
}
