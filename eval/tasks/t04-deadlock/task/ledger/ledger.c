/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

struct account {
    int id;
    long balance;
    pthread_mutex_t lock;
};

struct worker {
    struct account *from;
    struct account *to;
    int transfers;
    long amount;
};

static struct account accounts[2] = {
    { .id = 0, .balance = 100000, .lock = PTHREAD_MUTEX_INITIALIZER },
    { .id = 1, .balance = 100000, .lock = PTHREAD_MUTEX_INITIALIZER },
};

static long journal_entries;
static pthread_mutex_t journal_lock = PTHREAD_MUTEX_INITIALIZER;

static void journal_append(int id, long delta)
{
    struct timespec flush = { 0, 200000 };

    pthread_mutex_lock(&journal_lock);
    journal_entries++;
    pthread_mutex_unlock(&journal_lock);
    (void)id;
    (void)delta;
    nanosleep(&flush, NULL);
}

static void transfer(struct account *from, struct account *to, long amount)
{
    pthread_mutex_lock(&from->lock);
    journal_append(from->id, -amount);
    pthread_mutex_lock(&to->lock);
    journal_append(to->id, amount);
    from->balance -= amount;
    to->balance += amount;
    pthread_mutex_unlock(&to->lock);
    pthread_mutex_unlock(&from->lock);
}

static void *run_worker(void *arg)
{
    struct worker *w = arg;

    for (int i = 0; i < w->transfers; i++)
        transfer(w->from, w->to, w->amount);
    return NULL;
}

int main(int argc, char **argv)
{
    int n = argc > 1 ? atoi(argv[1]) : 500;
    struct worker w[2] = {
        { &accounts[0], &accounts[1], n, 7 },
        { &accounts[1], &accounts[0], n, 3 },
    };
    pthread_t tid[2];

    printf("ledger: %d transfers per worker\n", n);
    fflush(stdout);
    for (int i = 0; i < 2; i++)
        pthread_create(&tid[i], NULL, run_worker, &w[i]);
    for (int i = 0; i < 2; i++)
        pthread_join(tid[i], NULL);
    printf("balances: a0=%ld a1=%ld total=%ld journal=%ld\n", accounts[0].balance, accounts[1].balance,
           accounts[0].balance + accounts[1].balance, journal_entries);
    return 0;
}
