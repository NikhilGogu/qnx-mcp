/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#define WINDOW 65536

struct job {
    unsigned seed;
    int nsamples;
    double rms;
    double peak;
};

static double next_sample(unsigned *state)
{
    *state = *state * 1103515245u + 12345u;
    return ((int)((*state >> 8) & 0xffff) - 32768) / 32768.0;
}

static void *analyse(void *arg)
{
    struct job *job = arg;
    double window[WINDOW];
    double sum = 0.0, peak = 0.0;
    unsigned state = job->seed;
    int n = job->nsamples < WINDOW ? job->nsamples : WINDOW;

    for (int i = 0; i < job->nsamples; i++)
        window[i % WINDOW] = next_sample(&state) * (1.0 + (i % 100) / 100.0);
    for (int i = 0; i < n; i++) {
        sum += window[i] * window[i];
        if (fabs(window[i]) > peak)
            peak = fabs(window[i]);
    }
    job->rms = sqrt(sum / n);
    job->peak = peak;
    return NULL;
}

int main(int argc, char **argv)
{
    struct job job = { .seed = 42, .nsamples = WINDOW };
    pthread_t tid;
    int rc;

    if (argc > 1)
        job.seed = (unsigned)strtoul(argv[1], NULL, 0);
    if (argc > 2)
        job.nsamples = atoi(argv[2]);
    if (job.nsamples <= 0) {
        fprintf(stderr, "vibmon: sample count must be positive\n");
        return 1;
    }
    printf("vibmon: analysing %d samples (seed %u)\n", job.nsamples, job.seed);
    fflush(stdout);
    rc = pthread_create(&tid, NULL, analyse, &job);
    if (rc != 0) {
        fprintf(stderr, "vibmon: pthread_create: error %d\n", rc);
        return 1;
    }
    pthread_join(tid, NULL);
    printf("rms=%.6f peak=%.6f\n", job.rms, job.peak);
    return 0;
}
