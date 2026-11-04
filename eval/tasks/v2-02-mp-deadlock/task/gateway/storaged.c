/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "conf.h"
#include "gw.h"

enum { QUOTA_NONE, QUOTA_LOCAL, QUOTA_GATEWAY };

static const char *conf_path = "/etc/storaged.conf";
static const char *log_path = "/data/var/storaged/records.log";
static int quota_mode = QUOTA_LOCAL;
static long local_quota = 100;
static uint32_t stored, rejected;
static int gw_coid = -1;

static int gateway_remaining(uint32_t seq, int32_t *remaining)
{
    struct quota_msg q = { .type = MSG_QUOTA, .seq = seq };
    struct quota_reply r;

    if (gw_coid == -1 && (gw_coid = name_open(GW_CTL_NAME, 0)) == -1)
        return -1;
    if (MsgSend(gw_coid, &q, sizeof q, &r, sizeof r) == -1) {
        name_close(gw_coid);
        gw_coid = -1;
        return -1;
    }
    *remaining = r.remaining;
    return 0;
}

static int admit(uint32_t seq)
{
    int32_t remaining;

    switch (quota_mode) {
    case QUOTA_NONE:
        return EOK;
    case QUOTA_LOCAL:
        return stored < local_quota ? EOK : EDQUOT;
    default:
        if (gateway_remaining(seq, &remaining) == -1)
            return EIO;
        return remaining > 0 ? EOK : EDQUOT;
    }
}

static int store(FILE *log, const struct record_msg *rec)
{
    char payload[sizeof rec->payload + 1];

    memcpy(payload, rec->payload, sizeof rec->payload);
    payload[sizeof rec->payload] = '\0';
    if (fprintf(log, "%u %s\n", rec->seq, payload) < 0 || fflush(log) != 0)
        return EIO;
    return EOK;
}

int main(int argc, char **argv)
{
    char mode[32] = "local";
    name_attach_t *att;
    FILE *log;
    int opt;
    union {
        uint16_t type;
        struct _pulse pulse;
        struct record_msg rec;
    } msg;

    while ((opt = getopt(argc, argv, "c:l:")) != -1) {
        if (opt == 'c')
            conf_path = optarg;
        else if (opt == 'l')
            log_path = optarg;
    }
    conf_get(conf_path, "quota_check", mode, sizeof mode);
    quota_mode = strcmp(mode, "none") == 0 ? QUOTA_NONE : strcmp(mode, "gateway") == 0 ? QUOTA_GATEWAY : QUOTA_LOCAL;
    local_quota = conf_get_long(conf_path, "local_quota", 100);
    log = fopen(log_path, "a");
    if (log == NULL) {
        perror(log_path);
        return 1;
    }
    att = name_attach(NULL, STOR_NAME, 0);
    if (att == NULL) {
        perror("storaged: name_attach");
        return 1;
    }
    fprintf(stderr, "storaged: ready, quota check %s\n", mode);
    for (;;) {
        int rcvid = MsgReceive(att->chid, &msg, sizeof msg, NULL);
        struct store_reply rep;
        struct stats_reply st;

        if (rcvid == -1)
            continue;
        if (rcvid == 0) {
            if (msg.pulse.code == _PULSE_CODE_DISCONNECT)
                ConnectDetach(msg.pulse.scoid);
            continue;
        }
        if (msg.type == _IO_CONNECT) {
            MsgReply(rcvid, EOK, NULL, 0);
            continue;
        }
        if (msg.type == MSG_STATS) {
            st.stored = stored;
            st.rejected = rejected;
            MsgReply(rcvid, EOK, &st, sizeof st);
            continue;
        }
        if (msg.type != MSG_STORE) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        rep.status = admit(msg.rec.seq);
        if (rep.status == EOK)
            rep.status = store(log, &msg.rec);
        if (rep.status == EOK)
            stored++;
        else
            rejected++;
        rep.stored = stored;
        MsgReply(rcvid, EOK, &rep, sizeof rep);
    }
}
