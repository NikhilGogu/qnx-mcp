/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef ACQ_H
#define ACQ_H

#include <devctl.h>
#include <stdint.h>

#define ACQ_PATH "/dev/acq0"

struct acq_limits {
    uint32_t max_rate_hz;
    uint32_t max_gain;
    int32_t trig_min_mv;
    int32_t trig_max_mv;
    uint32_t has_ac;
    uint32_t has_trigger;
};

struct acq_status {
    uint32_t running;
    uint32_t rate_hz;
    uint32_t gain;
    uint32_t coupling;
    uint32_t trig_enabled;
    int32_t trig_mv;
};

#define COUPLING_DC 0
#define COUPLING_AC 1

#define DCMD_ACQ_GET_LIMITS   __DIOF(_DCMD_MISC, 0x60, struct acq_limits)
#define DCMD_ACQ_GET_STATUS   __DIOF(_DCMD_MISC, 0x61, struct acq_status)
#define DCMD_ACQ_SET_RATE     __DIOT(_DCMD_MISC, 0x62, uint32_t)
#define DCMD_ACQ_SET_GAIN     __DIOT(_DCMD_MISC, 0x63, uint32_t)
#define DCMD_ACQ_SET_COUPLING __DIOT(_DCMD_MISC, 0x64, uint32_t)
#define DCMD_ACQ_SET_TRIGGER  __DIOT(_DCMD_MISC, 0x65, int32_t)
#define DCMD_ACQ_TRIGGER_OFF  __DION(_DCMD_MISC, 0x66)
#define DCMD_ACQ_START        __DION(_DCMD_MISC, 0x67)
#define DCMD_ACQ_STOP         __DION(_DCMD_MISC, 0x68)

#endif
