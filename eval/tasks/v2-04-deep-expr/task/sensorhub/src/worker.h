/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef WORKER_H
#define WORKER_H

#include <pthread.h>
#include <stddef.h>

struct worker {
    const char *name;
    pthread_t tid;
    int (*step)(void *ctx, int cycle);
    void *ctx;
    int go;
    int done;
    int errors;
};

int worker_start(struct worker *w, size_t stack_kb);
void worker_run(struct worker **ws, int n, int cycle);
void worker_stop_all(struct worker **ws, int n);

#endif
