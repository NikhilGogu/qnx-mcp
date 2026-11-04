/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/iofunc.h>
#include <sys/slog2.h>
#include "acq.h"
#include "board.h"

static struct acq_limits lim;
static struct acq_status st = { .rate_hz = 100, .gain = 1, .coupling = COUPLING_DC };
static slog2_buffer_t logbuf;
static resmgr_connect_funcs_t connect_funcs;
static resmgr_io_funcs_t io_funcs;
static iofunc_attr_t attr;

static int board_rev = 'C';

/* Rev E boards (field units) set rate, gain and trigger through a window
 * comparator that answers with a hardware status. */
enum { HW_OK, HW_RANGE, HW_ABSENT, HW_BUSY, HW_FAULT };

static const int hw_errno[] = {
    [HW_OK] = EOK,
    [HW_RANGE] = ENOTSUP,
    [HW_ABSENT] = ENOTSUP,
    [HW_BUSY] = EBUSY,
    [HW_FAULT] = EIO,
};

static int hw_window(long v, long lo, long hi)
{
    return v < lo || v > hi ? HW_RANGE : HW_OK;
}

static int hw_result(const char *what, long v, int hw)
{
    if (hw != HW_OK)
        slog2f(logbuf, 0, SLOG2_WARNING, "rev E %s: %ld not accepted by the comparator (hw status %d)", what, v, hw);
    return hw_errno[hw];
}

static int valid_gain(uint32_t g)
{
    return g == 1 || g == 2 || g == 4 || g == 8 || g == 16 || g == 32;
}

static int set_rate(uint32_t hz)
{
    if (hz == 0 || hz % 10 != 0)
        return EINVAL;
    if (hz > lim.max_rate_hz) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_RATE %u Hz rejected: above board maximum %u Hz", hz, lim.max_rate_hz);
        return ERANGE;
    }
    if (st.running)
        return EBUSY;
    st.rate_hz = hz;
    return EOK;
}

static int set_gain(uint32_t g)
{
    if (!valid_gain(g))
        return EINVAL;
    if (g > lim.max_gain) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_GAIN %u rejected: above board maximum %u", g, lim.max_gain);
        return ERANGE;
    }
    if (st.running)
        return EBUSY;
    st.gain = g;
    return EOK;
}

static int set_coupling(uint32_t c)
{
    if (c != COUPLING_DC && c != COUPLING_AC)
        return EINVAL;
    if (c == COUPLING_AC && !lim.has_ac) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_COUPLING ac rejected: board has no AC coupling");
        return ENOTSUP;
    }
    if (st.running)
        return EBUSY;
    st.coupling = c;
    return EOK;
}

static int set_trigger(int32_t mv)
{
    if (!lim.has_trigger) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_TRIGGER rejected: board has no trigger input");
        return ENOTSUP;
    }
    if (mv < lim.trig_min_mv) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_TRIGGER %d mV rejected: below board minimum %d mV", mv, lim.trig_min_mv);
        return ERANGE;
    }
    if (mv > lim.trig_max_mv) {
        slog2f(logbuf, 0, SLOG2_WARNING, "SET_TRIGGER %d mV rejected: above board maximum %d mV", mv, lim.trig_max_mv);
        return ERANGE;
    }
    st.trig_mv = mv;
    st.trig_enabled = 1;
    return EOK;
}

static int rev_e_set_rate(uint32_t hz)
{
    int hw;

    if (hz == 0 || hz % 10 != 0)
        return EINVAL;
    hw = hw_window(hz, 10, lim.max_rate_hz);
    if (hw == HW_OK && st.running)
        hw = HW_BUSY;
    if (hw == HW_OK)
        st.rate_hz = hz;
    return hw_result("rate", hz, hw);
}

static int rev_e_set_gain(uint32_t g)
{
    int hw;

    if (!valid_gain(g))
        return EINVAL;
    hw = hw_window(g, 1, lim.max_gain);
    if (hw == HW_OK && st.running)
        hw = HW_BUSY;
    if (hw == HW_OK)
        st.gain = g;
    return hw_result("gain", g, hw);
}

static int rev_e_set_trigger(int32_t mv)
{
    int hw = lim.has_trigger ? hw_window(mv, lim.trig_min_mv, lim.trig_max_mv) : HW_ABSENT;

    if (hw == HW_OK) {
        st.trig_mv = mv;
        st.trig_enabled = 1;
    }
    return hw_result("trigger", mv, hw);
}

static int io_devctl(resmgr_context_t *ctp, io_devctl_t *msg, RESMGR_OCB_T *ocb)
{
    int status = iofunc_devctl_default(ctp, msg, ocb);
    void *data = _DEVCTL_DATA(msg->i);
    unsigned nbytes = 0, need = 0;

    if (status != _RESMGR_DEFAULT)
        return status;
    switch (msg->i.dcmd) {
    case DCMD_ACQ_SET_RATE:
    case DCMD_ACQ_SET_GAIN:
    case DCMD_ACQ_SET_COUPLING:
    case DCMD_ACQ_SET_TRIGGER:
        need = sizeof(uint32_t);
        break;
    }
    if (msg->i.nbytes < need)
        return EINVAL;
    switch (msg->i.dcmd) {
    case DCMD_ACQ_GET_LIMITS:
        memcpy(data, &lim, sizeof lim);
        nbytes = sizeof lim;
        break;
    case DCMD_ACQ_GET_STATUS:
        memcpy(data, &st, sizeof st);
        nbytes = sizeof st;
        break;
    case DCMD_ACQ_SET_RATE:
        status = board_rev >= 'E' ? rev_e_set_rate(*(uint32_t *)data) : set_rate(*(uint32_t *)data);
        break;
    case DCMD_ACQ_SET_GAIN:
        status = board_rev >= 'E' ? rev_e_set_gain(*(uint32_t *)data) : set_gain(*(uint32_t *)data);
        break;
    case DCMD_ACQ_SET_COUPLING:
        status = set_coupling(*(uint32_t *)data);
        break;
    case DCMD_ACQ_SET_TRIGGER:
        status = board_rev >= 'E' ? rev_e_set_trigger(*(int32_t *)data) : set_trigger(*(int32_t *)data);
        break;
    case DCMD_ACQ_TRIGGER_OFF:
        st.trig_enabled = 0;
        status = EOK;
        break;
    case DCMD_ACQ_START:
        st.running = 1;
        status = EOK;
        break;
    case DCMD_ACQ_STOP:
        st.running = 0;
        status = EOK;
        break;
    default:
        return ENOSYS;
    }
    if (status != _RESMGR_DEFAULT && status != EOK)
        return status;
    memset(&msg->o, 0, sizeof msg->o);
    msg->o.nbytes = nbytes;
    return _RESMGR_PTR(ctp, &msg->o, sizeof msg->o + nbytes);
}

int main(int argc, char **argv)
{
    const char *board = "/etc/acq.board";
    slog2_buffer_set_config_t logcfg = { 0 };
    resmgr_attr_t rattr = { 0 };
    dispatch_t *dpp;
    dispatch_context_t *ctp;
    int opt;

    while ((opt = getopt(argc, argv, "b:")) != -1)
        if (opt == 'b')
            board = optarg;
    logcfg.buffer_set_name = "acqd";
    logcfg.num_buffers = 1;
    logcfg.verbosity_level = SLOG2_INFO;
    logcfg.buffer_config[0].buffer_name = "main";
    logcfg.buffer_config[0].num_pages = 4;
    if (slog2_register(&logcfg, &logbuf, 0) == -1) {
        fprintf(stderr, "acqd: slog2_register failed\n");
        return 1;
    }
    if (board_load(board, &lim, &board_rev) != 0)
        slog2f(logbuf, 0, SLOG2_WARNING, "no board description %s, using defaults", board);
    dpp = dispatch_create();
    if (dpp == NULL) {
        perror("acqd: dispatch_create");
        return 1;
    }
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &io_funcs);
    io_funcs.devctl = io_devctl;
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    rattr.nparts_max = 1;
    rattr.msg_max_size = 2048;
    if (resmgr_attach(dpp, &rattr, ACQ_PATH, _FTYPE_ANY, 0, &connect_funcs, &io_funcs, &attr) == -1) {
        perror("acqd: resmgr_attach");
        return 1;
    }
    slog2f(logbuf, 0, SLOG2_INFO, "serving %s: rev %c, max %u Hz, gain <= %u, trigger %d..%d mV%s%s", ACQ_PATH, board_rev,
           lim.max_rate_hz, lim.max_gain, lim.trig_min_mv, lim.trig_max_mv, lim.has_ac ? ", ac" : "", lim.has_trigger ? "" : ", no trigger");
    ctp = dispatch_context_alloc(dpp);
    for (;;) {
        ctp = dispatch_block(ctp);
        if (ctp == NULL) {
            perror("acqd: dispatch_block");
            return 1;
        }
        dispatch_handler(ctp);
    }
}
