/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef QUEUE_H
#define QUEUE_H

#include <pthread.h>

struct item {
    long id;
    long value;
};

struct queue {
    pthread_mutex_t lock;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    struct item *items;
    int cap;
    int head;
    int count;
    int closed;
};

int queue_init(struct queue *q, int cap);
void queue_push(struct queue *q, const struct item *it);
int queue_pop_batch(struct queue *q, struct item *out, int want);
int queue_length(struct queue *q);
void queue_close(struct queue *q);

#endif
