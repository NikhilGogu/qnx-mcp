/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "conf.h"
#include "queue.h"

#define MAX_WORKERS 8

static struct pipeline_conf cfg;
static struct queue raw_q, out_q;
static pthread_mutex_t stats_lock = PTHREAD_MUTEX_INITIALIZER;
static long n_read, n_bad, n_written, n_batches;
static const struct item end_marker = { -1, 0 };

static void count(long *what, long n)
{
    pthread_mutex_lock(&stats_lock);
    *what += n;
    pthread_mutex_unlock(&stats_lock);
}

static void sleep_ms(int ms)
{
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };

    nanosleep(&ts, NULL);
}

static void *reader(void *arg)
{
    FILE *in = arg;
    char line[128];
    long lineno = 0;

    while (fgets(line, sizeof line, in)) {
        struct item it;

        lineno++;
        if (sscanf(line, "%ld,%ld", &it.id, &it.value) != 2 || it.id < 0) {
            count(&n_bad, 1);
            continue;
        }
        queue_push(&raw_q, &it);
        count(&n_read, 1);
        if (cfg.rate_limit_ms > 0 && lineno % 100 == 0)
            sleep_ms(cfg.rate_limit_ms);
    }
    for (int i = 0; i < cfg.workers; i++)
        queue_push(&raw_q, &end_marker);
    return NULL;
}

static void *transformer(void *arg)
{
    struct item it;

    (void)arg;
    while (queue_pop_batch(&raw_q, &it, 1) == 1) {
        if (it.id == end_marker.id)
            break;
        it.value = it.value * 3 + 7;
        queue_push(&out_q, &it);
    }
    return NULL;
}

static void *writer(void *arg)
{
    FILE *out = arg;
    struct item *batch = calloc((size_t)cfg.batch, sizeof *batch);
    int n;

    if (batch == NULL)
        return NULL;
    while ((n = queue_pop_batch(&out_q, batch, cfg.batch)) > 0) {
        for (int i = 0; i < n; i++)
            fprintf(out, "%ld,%ld\n", batch[i].id, batch[i].value);
        fflush(out);
        count(&n_written, n);
        count(&n_batches, 1);
    }
    free(batch);
    return NULL;
}

int main(int argc, char **argv)
{
    const char *conf = argc > 1 ? argv[1] : "/etc/pipeline.conf";
    pthread_t rt, tt[MAX_WORKERS], wt;
    FILE *in, *out;

    if (conf_load(conf, &cfg) != 0)
        fprintf(stderr, "pipeline: %s not found, using defaults\n", conf);
    if (cfg.workers < 1 || cfg.workers > MAX_WORKERS)
        cfg.workers = 1;
    in = fopen(cfg.input, "r");
    out = fopen(cfg.output, "w");
    if (in == NULL || out == NULL) {
        fprintf(stderr, "pipeline: cannot open %s or %s: %s\n", cfg.input, cfg.output, strerror(errno));
        return 1;
    }
    if (queue_init(&raw_q, cfg.depth) || queue_init(&out_q, cfg.depth))
        return 1;
    pthread_create(&rt, NULL, reader, in);
    pthread_setname_np(rt, "reader");
    for (int i = 0; i < cfg.workers; i++) {
        char name[16];

        pthread_create(&tt[i], NULL, transformer, NULL);
        snprintf(name, sizeof name, "transform%d", i);
        pthread_setname_np(tt[i], name);
    }
    pthread_create(&wt, NULL, writer, out);
    pthread_setname_np(wt, "writer");
    pthread_join(rt, NULL);
    for (int i = 0; i < cfg.workers; i++)
        pthread_join(tt[i], NULL);
    queue_close(&out_q);
    pthread_join(wt, NULL);
    fclose(in);
    fclose(out);
    printf("pipeline: read %ld written %ld bad %ld batches %ld\n", n_read, n_written, n_bad, n_batches);
    return n_written == n_read ? 0 : 1;
}
