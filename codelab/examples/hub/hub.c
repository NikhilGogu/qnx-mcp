/*
 * hub: a sensor hub in one process. The sampler thread refreshes a table of
 * readings and publishes each new table to the server thread; the server
 * thread answers requests on a QNX channel; the display thread asks the
 * server for the latest table and prints it.
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/neutrino.h>

#define NSENSORS 4

enum { MSG_GET = 1, MSG_PUBLISH = 2 };

struct request {
    int type;
    int seq;
};

struct table {
    int seq;
    int value[NSENSORS];
};

static pthread_mutex_t table_lock = PTHREAD_MUTEX_INITIALIZER;
static struct table latest;
static int published;
static int chid;

static int read_sensor(int sensor, int seq)
{
    usleep(5 * 1000); /* one transfer on the sensor bus */
    return 100 * (sensor + 1) + seq % 10;
}

static void *server(void *arg)
{
    for (;;) {
        struct request req;
        int rcvid = MsgReceive(chid, &req, sizeof req, NULL);
        if (rcvid <= 0) {
            continue;
        }
        struct table reply = {0};
        if (req.type == MSG_GET) {
            pthread_mutex_lock(&table_lock);
            reply = latest;
            pthread_mutex_unlock(&table_lock);
        } else if (req.type == MSG_PUBLISH) {
            published = req.seq;
            reply.seq = req.seq;
        }
        MsgReply(rcvid, EOK, &reply, sizeof reply);
    }
    return NULL;
}

static void *sampler(void *arg)
{
    int coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    for (int seq = 1;; seq++) {
        pthread_mutex_lock(&table_lock);
        for (int i = 0; i < NSENSORS; i++) {
            latest.value[i] = read_sensor(i, seq);
        }
        latest.seq = seq;
        struct request req = {MSG_PUBLISH, seq};
        struct table ack;
        MsgSend(coid, &req, sizeof req, &ack, sizeof ack);
        pthread_mutex_unlock(&table_lock);
        usleep(100 * 1000);
    }
    return NULL;
}

static void *display(void *arg)
{
    int coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    for (;;) {
        struct request req = {MSG_GET, 0};
        struct table t;
        if (MsgSend(coid, &req, sizeof req, &t, sizeof t) == -1) {
            perror("MsgSend");
            return NULL;
        }
        printf("table %d: %d %d %d %d\n", t.seq, t.value[0], t.value[1], t.value[2], t.value[3]);
        fflush(stdout);
        usleep(30 * 1000);
    }
    return NULL;
}

int main(void)
{
    pthread_t threads[3];

    chid = ChannelCreate(0);
    if (chid == -1) {
        perror("ChannelCreate");
        return 1;
    }
    pthread_create(&threads[0], NULL, server, NULL);
    pthread_create(&threads[1], NULL, sampler, NULL);
    pthread_create(&threads[2], NULL, display, NULL);
    printf("hub: pid %d, server thread %d, sampler thread %d, display thread %d\n", getpid(), (int)threads[0],
           (int)threads[1], (int)threads[2]);
    fflush(stdout);
    for (int i = 0; i < 3; i++) {
        pthread_join(threads[i], NULL);
    }
    return 0;
}
