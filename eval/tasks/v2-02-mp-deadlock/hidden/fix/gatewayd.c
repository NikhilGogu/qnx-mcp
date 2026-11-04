/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "conf.h"
#include "gw.h"

static const char *conf_path = "/etc/gateway.conf";
static long quota;
static long accepted;
static pthread_mutex_t count_lock = PTHREAD_MUTEX_INITIALIZER;

static int handle_system(int rcvid, uint16_t type, struct _pulse *pulse)
{
    if (rcvid == 0) {
        if (pulse->code == _PULSE_CODE_DISCONNECT)
            ConnectDetach(pulse->scoid);
        return 1;
    }
    if (type == _IO_CONNECT) {
        MsgReply(rcvid, EOK, NULL, 0);
        return 1;
    }
    return 0;
}

/* Control channel: answers storaged's quota queries while the main thread is
 * busy forwarding a record. */
static void *control_thread(void *arg)
{
    name_attach_t *att = arg;
    union {
        uint16_t type;
        struct _pulse pulse;
        struct quota_msg q;
    } msg;

    for (;;) {
        int rcvid = MsgReceive(att->chid, &msg, sizeof msg, NULL);
        struct quota_reply r;

        if (rcvid == -1 || handle_system(rcvid, msg.type, &msg.pulse))
            continue;
        if (msg.type != MSG_QUOTA) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        pthread_mutex_lock(&count_lock);
        r.remaining = (int32_t)(quota - accepted);
        pthread_mutex_unlock(&count_lock);
        MsgReply(rcvid, EOK, &r, sizeof r);
    }
    return NULL;
}

static int forward(int *coid, struct record_msg *rec, struct store_reply *rep)
{
    if (*coid == -1 && (*coid = name_open(STOR_NAME, 0)) == -1)
        return -1;
    rec->type = MSG_STORE;
    if (MsgSend(*coid, rec, sizeof *rec, rep, sizeof *rep) == -1) {
        name_close(*coid);
        *coid = -1;
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    name_attach_t *att, *ctl;
    pthread_t ctl_tid;
    int stor = -1, opt;
    union {
        uint16_t type;
        struct _pulse pulse;
        struct record_msg rec;
    } msg;

    while ((opt = getopt(argc, argv, "c:")) != -1)
        if (opt == 'c')
            conf_path = optarg;
    quota = conf_get_long(conf_path, "quota", 1000);
    att = name_attach(NULL, GW_NAME, 0);
    ctl = name_attach(NULL, GW_CTL_NAME, 0);
    if (att == NULL || ctl == NULL) {
        perror("gatewayd: name_attach");
        return 1;
    }
    if (pthread_create(&ctl_tid, NULL, control_thread, ctl) != 0) {
        fprintf(stderr, "gatewayd: cannot start control thread\n");
        return 1;
    }
    pthread_setname_np(ctl_tid, "control");
    fprintf(stderr, "gatewayd: ready, quota %ld records\n", quota);
    for (;;) {
        int rcvid = MsgReceive(att->chid, &msg, sizeof msg, NULL);
        struct store_reply rep;

        if (rcvid == -1 || handle_system(rcvid, msg.type, &msg.pulse))
            continue;
        if (msg.type != MSG_RECORD) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        if (forward(&stor, &msg.rec, &rep) == -1) {
            MsgError(rcvid, EIO);
            continue;
        }
        if (rep.status == EOK) {
            pthread_mutex_lock(&count_lock);
            accepted++;
            pthread_mutex_unlock(&count_lock);
        }
        MsgReply(rcvid, EOK, &rep, sizeof rep);
    }
}
