/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <time.h>
#include "core/clock.h"
#if defined(__unix__)
#include <sys/sysinfo.h>
#endif

long clock_uptime_s(void)
{
    struct timespec ts;
#if defined(__unix__)
    struct sysinfo si;

    if (sysinfo(&si) == 0)
        return si.uptime;
#endif
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec;
}

uint64_t clock_ms(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

void clock_sleep_ms(unsigned ms)
{
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };

    nanosleep(&ts, NULL);
}
