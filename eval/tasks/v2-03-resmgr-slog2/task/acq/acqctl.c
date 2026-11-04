/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/slog2.h>
#include "acq.h"

static slog2_buffer_t logbuf;

static void logf_(int sev, const char *fmt, const char *what, int a, int err)
{
    char line[200];

    snprintf(line, sizeof line, fmt, what, a, strerror(err));
    slog2c(logbuf, 0, (unsigned char)sev, line);
}

static int dev(int fd, int dcmd, void *data, size_t n)
{
    return devctl(fd, dcmd, data, n, NULL);
}

static int apply_u32(int fd, int dcmd, const char *what, uint32_t v, uint32_t limit)
{
    int rc = dev(fd, dcmd, &v, sizeof v);

    if (rc == ERANGE) {
        logf_(SLOG2_WARNING, "%s %d out of range (%s); using the board maximum", what, (int)v, rc);
        v = limit;
        rc = dev(fd, dcmd, &v, sizeof v);
    }
    if (rc == ENOTSUP) {
        logf_(SLOG2_WARNING, "%s %d: not supported by this board (%s); skipped", what, (int)v, rc);
        return 0;
    }
    if (rc != EOK) {
        logf_(SLOG2_ERROR, "%s %d failed: %s", what, (int)v, rc);
        return -1;
    }
    return 0;
}

static int apply_trigger(int fd, int32_t mv, const struct acq_limits *lim)
{
    int rc = dev(fd, DCMD_ACQ_SET_TRIGGER, &mv, sizeof mv);

    if (rc == ERANGE) {
        logf_(SLOG2_WARNING, "%s %d mV out of range (%s); clamping to the board limits", "trigger", mv, rc);
        mv = mv > lim->trig_max_mv ? lim->trig_max_mv : lim->trig_min_mv;
        rc = dev(fd, DCMD_ACQ_SET_TRIGGER, &mv, sizeof mv);
    }
    if (rc == ENOTSUP) {
        logf_(SLOG2_WARNING, "%s %d mV: not supported by this board (%s); trigger disabled", "trigger", mv, rc);
        dev(fd, DCMD_ACQ_TRIGGER_OFF, NULL, 0);
        return 0;
    }
    if (rc != EOK) {
        logf_(SLOG2_ERROR, "%s %d mV failed: %s", "trigger", mv, rc);
        return -1;
    }
    return 0;
}

static long conf_value(const char *path, const char *key, long dflt)
{
    char line[128], k[32];
    long v, out = dflt;
    FILE *f = fopen(path, "r");

    if (f == NULL)
        return dflt;
    while (fgets(line, sizeof line, f))
        if (sscanf(line, " %31[a-z_] = %ld", k, &v) == 2 && strcmp(k, key) == 0)
            out = v;
    fclose(f);
    return out;
}

static int apply(int fd, const char *conf)
{
    struct acq_limits lim;
    int bad = 0;

    if (dev(fd, DCMD_ACQ_GET_LIMITS, &lim, sizeof lim) != EOK)
        return -1;
    dev(fd, DCMD_ACQ_STOP, NULL, 0);
    bad |= apply_u32(fd, DCMD_ACQ_SET_RATE, "rate", (uint32_t)conf_value(conf, "rate_hz", 1000), lim.max_rate_hz);
    bad |= apply_u32(fd, DCMD_ACQ_SET_GAIN, "gain", (uint32_t)conf_value(conf, "gain", 1), lim.max_gain);
    bad |= apply_u32(fd, DCMD_ACQ_SET_COUPLING, "coupling", (uint32_t)conf_value(conf, "coupling_ac", 0), COUPLING_DC);
    if (conf_value(conf, "trigger", 0))
        bad |= apply_trigger(fd, (int32_t)conf_value(conf, "trigger_mv", 0), &lim);
    else
        dev(fd, DCMD_ACQ_TRIGGER_OFF, NULL, 0);
    if (dev(fd, DCMD_ACQ_START, NULL, 0) != EOK)
        bad = 1;
    return bad ? -1 : 0;
}

int main(int argc, char **argv)
{
    slog2_buffer_set_config_t cfg = { 0 };
    struct acq_status st;
    int fd;

    cfg.buffer_set_name = "acqctl";
    cfg.num_buffers = 1;
    cfg.verbosity_level = SLOG2_INFO;
    cfg.buffer_config[0].buffer_name = "main";
    cfg.buffer_config[0].num_pages = 2;
    slog2_register(&cfg, &logbuf, 0);
    if (argc < 2 || (strcmp(argv[1], "apply") && strcmp(argv[1], "status"))) {
        fprintf(stderr, "usage: acqctl apply [conf] | acqctl status\n");
        return 2;
    }
    fd = open(ACQ_PATH, O_RDWR);
    if (fd == -1) {
        perror("acqctl: " ACQ_PATH);
        return 1;
    }
    if (strcmp(argv[1], "apply") == 0) {
        if (apply(fd, argc > 2 ? argv[2] : "/etc/acq.conf") != 0) {
            fprintf(stderr, "acqctl: configuration failed, see the system log\n");
            return 1;
        }
        printf("acqctl: configuration applied\n");
        return 0;
    }
    if (dev(fd, DCMD_ACQ_GET_STATUS, &st, sizeof st) != EOK) {
        fprintf(stderr, "acqctl: status failed\n");
        return 1;
    }
    printf("acq: %s rate %u Hz gain %u coupling %s trigger ", st.running ? "running" : "stopped", st.rate_hz, st.gain,
           st.coupling == COUPLING_AC ? "ac" : "dc");
    if (st.trig_enabled)
        printf("%d mV\n", st.trig_mv);
    else
        printf("off\n");
    return 0;
}
