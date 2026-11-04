/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <pthread.h>
#include <string.h>
#include "log.h"
#include "worker.h"

static pthread_mutex_t wlock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t go_cond = PTHREAD_COND_INITIALIZER;
static pthread_cond_t done_cond = PTHREAD_COND_INITIALIZER;
static int stopping;

static void *worker_main(void *arg)
{
    struct worker *w = arg;

    pthread_mutex_lock(&wlock);
    for (;;) {
        int c;

        while (w->go == w->done && !stopping)
            pthread_cond_wait(&go_cond, &wlock);
        if (stopping)
            break;
        c = w->go;
        pthread_mutex_unlock(&wlock);
        if (w->step(w->ctx, c) != 0)
            w->errors++;
        pthread_mutex_lock(&wlock);
        w->done = c;
        pthread_cond_broadcast(&done_cond);
    }
    pthread_mutex_unlock(&wlock);
    return NULL;
}

/* stack_kb 0 keeps the default thread stack. */
int worker_start(struct worker *w, size_t stack_kb)
{
    pthread_attr_t attr;
    int rc;

    w->go = w->done = -1;
    pthread_attr_init(&attr);
    if (stack_kb)
        pthread_attr_setstacksize(&attr, stack_kb * 1024);
    rc = pthread_create(&w->tid, &attr, worker_main, w);
    pthread_attr_destroy(&attr);
    if (rc != 0) {
        log_msg(LOG_ERR, "cannot start worker %s: %s", w->name, strerror(rc));
        return -1;
    }
#ifdef __QNX__
    pthread_setname_np(w->tid, w->name);
#endif
    return 0;
}

void worker_run(struct worker **ws, int n, int cycle)
{
    pthread_mutex_lock(&wlock);
    for (int i = 0; i < n; i++)
        ws[i]->go = cycle;
    pthread_cond_broadcast(&go_cond);
    for (int i = 0; i < n; i++)
        while (ws[i]->done != cycle)
            pthread_cond_wait(&done_cond, &wlock);
    pthread_mutex_unlock(&wlock);
}

void worker_stop_all(struct worker **ws, int n)
{
    pthread_mutex_lock(&wlock);
    stopping = 1;
    pthread_cond_broadcast(&go_cond);
    pthread_mutex_unlock(&wlock);
    for (int i = 0; i < n; i++)
        pthread_join(ws[i]->tid, NULL);
}
