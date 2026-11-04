/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include "log.h"
#ifdef __QNX__
#include <sys/slog2.h>
#endif

static const char *progname = "sensorhub";
static int verbosity;
static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
#ifdef __QNX__
static slog2_buffer_t logbuf;
static int have_slog;
#endif

void log_init(const char *name, int verbose)
{
    progname = name;
    verbosity = verbose;
#ifdef __QNX__
    slog2_buffer_set_config_t cfg = { 0 };

    cfg.buffer_set_name = name;
    cfg.num_buffers = 1;
    cfg.verbosity_level = SLOG2_DEBUG1;
    cfg.buffer_config[0].buffer_name = "main";
    cfg.buffer_config[0].num_pages = 8;
    have_slog = slog2_register(&cfg, &logbuf, 0) == 0;
#endif
}

void log_msg(int level, const char *fmt, ...)
{
    char line[512];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    pthread_mutex_lock(&log_lock);
#ifdef __QNX__
    if (have_slog) {
        static const unsigned char sev[] = { SLOG2_ERROR, SLOG2_WARNING, SLOG2_INFO, SLOG2_DEBUG1 };
        slog2c(logbuf, 0, sev[level], line);
    }
#endif
    if (level <= LOG_WARN || level <= LOG_WARN + verbosity)
        fprintf(stderr, "%s: %s\n", progname, line);
    pthread_mutex_unlock(&log_lock);
}

void log_close(void)
{
    fflush(stderr);
}
