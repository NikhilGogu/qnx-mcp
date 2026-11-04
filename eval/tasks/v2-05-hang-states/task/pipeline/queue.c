/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <stdlib.h>
#include "queue.h"

int queue_init(struct queue *q, int cap)
{
    q->items = calloc((size_t)cap, sizeof *q->items);
    if (q->items == NULL)
        return -1;
    q->cap = cap;
    q->head = q->count = q->closed = 0;
    pthread_mutex_init(&q->lock, NULL);
    pthread_cond_init(&q->not_empty, NULL);
    pthread_cond_init(&q->not_full, NULL);
    return 0;
}

void queue_push(struct queue *q, const struct item *it)
{
    pthread_mutex_lock(&q->lock);
    while (q->count == q->cap)
        pthread_cond_wait(&q->not_full, &q->lock);
    q->items[(q->head + q->count) % q->cap] = *it;
    q->count++;
    pthread_cond_signal(&q->not_empty);
    pthread_mutex_unlock(&q->lock);
}

/* Waits for `want` items, or fewer once the queue is closed; returns how many
 * were taken (0 means closed and empty). */
int queue_pop_batch(struct queue *q, struct item *out, int want)
{
    int n = 0;

    pthread_mutex_lock(&q->lock);
    while (q->count < want && !q->closed)
        pthread_cond_wait(&q->not_empty, &q->lock);
    while (n < want && q->count > 0) {
        out[n++] = q->items[q->head];
        q->head = (q->head + 1) % q->cap;
        q->count--;
    }
    pthread_cond_broadcast(&q->not_full);
    pthread_mutex_unlock(&q->lock);
    return n;
}

int queue_length(struct queue *q)
{
    int n;

    pthread_mutex_lock(&q->lock);
    n = q->count;
    pthread_mutex_unlock(&q->lock);
    return n;
}

void queue_close(struct queue *q)
{
    pthread_mutex_lock(&q->lock);
    q->closed = 1;
    pthread_cond_broadcast(&q->not_empty);
    pthread_mutex_unlock(&q->lock);
}
