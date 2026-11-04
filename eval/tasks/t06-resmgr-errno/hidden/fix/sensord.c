/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/iofunc.h>
#include <sys/slog2.h>
#include "sensor.h"

static const uint32_t supported[] = { 1, 10, 50, 100 };
static uint32_t current_rate = 10;
static slog2_buffer_t logbuf;
static resmgr_connect_funcs_t connect_funcs;
static resmgr_io_funcs_t io_funcs;
static iofunc_attr_t attr;

static int rate_supported(uint32_t hz)
{
    for (size_t i = 0; i < sizeof supported / sizeof supported[0]; i++)
        if (supported[i] == hz)
            return 1;
    return 0;
}

static int set_rate(uint32_t hz)
{
    if (!rate_supported(hz)) {
        slog2f(logbuf, 0, SLOG2_WARNING, "sensord: rejecting unsupported rate %u Hz", hz);
        errno = EINVAL;
        return -1;
    }
    current_rate = hz;
    slog2f(logbuf, 0, SLOG2_INFO, "sensord: rate set to %u Hz", hz);
    return 0;
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, RESMGR_OCB_T *ocb)
{
    int status = iofunc_devctl_default(ctp, msg, ocb);
    void *data = _DEVCTL_DATA(msg->i);
    unsigned nbytes = 0;

    if (status != _RESMGR_DEFAULT)
        return status;
    switch (msg->i.dcmd) {
    case DCMD_SENSOR_SET_RATE:
        if (msg->i.nbytes < sizeof(uint32_t))
            return EINVAL;
        if (set_rate(*(uint32_t *)data) == -1)
            return EINVAL;
        break;
    case DCMD_SENSOR_GET_RATE:
        *(uint32_t *)data = current_rate;
        nbytes = sizeof(uint32_t);
        break;
    default:
        return ENOSYS;
    }
    memset(&msg->o, 0, sizeof msg->o);
    msg->o.nbytes = nbytes;
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o + nbytes);
}

int main(void)
{
    slog2_buffer_set_config_t logcfg = { 0 };
    resmgr_attr_t rattr = { 0 };
    dispatch_t *dpp;
    dispatch_context_t *ctp;

    logcfg.buffer_set_name = "sensord";
    logcfg.num_buffers = 1;
    logcfg.verbosity_level = SLOG2_INFO;
    logcfg.buffer_config[0].buffer_name = "main";
    logcfg.buffer_config[0].num_pages = 4;
    if (slog2_register(&logcfg, &logbuf, 0) == -1) {
        fprintf(stderr, "sensord: slog2_register failed\n");
        return 1;
    }
    dpp = dispatch_create();
    if (dpp == NULL) {
        perror("sensord: dispatch_create");
        return 1;
    }
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &io_funcs);
    io_funcs.devctl = io_devctl;
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    rattr.nparts_max = 1;
    rattr.msg_max_size = 2048;
    if (resmgr_attach(dpp, &rattr, SENSOR_PATH, _FTYPE_ANY, 0, &connect_funcs, &io_funcs, &attr) == -1) {
        perror("sensord: resmgr_attach");
        return 1;
    }
    slog2f(logbuf, 0, SLOG2_INFO, "sensord: serving %s at %u Hz", SENSOR_PATH, current_rate);
    ctp = dispatch_context_alloc(dpp);
    for (;;) {
        ctp = dispatch_block(ctp);
        if (ctp == NULL) {
            perror("sensord: dispatch_block");
            return 1;
        }
        dispatch_handler(ctp);
    }
}
