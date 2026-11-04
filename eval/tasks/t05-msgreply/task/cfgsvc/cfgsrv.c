/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "cfgsvc.h"

static struct {
    const char *key;
    char value[64];
} store[] = {
    { "rate", "50" },
    { "mode", "auto" },
    { "gain", "2.5" },
};

static int find_key(const char *key)
{
    for (size_t i = 0; i < sizeof store / sizeof store[0]; i++)
        if (strcmp(store[i].key, key) == 0)
            return (int)i;
    return -1;
}

static void handle(int rcvid, struct cfg_msg *msg)
{
    struct cfg_reply reply;
    int idx;

    msg->key[sizeof msg->key - 1] = '\0';
    msg->value[sizeof msg->value - 1] = '\0';
    idx = find_key(msg->key);
    switch (msg->op) {
    case CFG_GET:
        if (idx < 0) {
            fprintf(stderr, "cfgsrv: get: unknown key '%s'\n", msg->key);
            break;
        }
        snprintf(reply.value, sizeof reply.value, "%s", store[idx].value);
        MsgReply(rcvid, EOK, &reply, sizeof reply);
        break;
    case CFG_SET:
        if (idx < 0) {
            MsgError(rcvid, ENOENT);
            break;
        }
        snprintf(store[idx].value, sizeof store[idx].value, "%s", msg->value);
        snprintf(reply.value, sizeof reply.value, "%s", store[idx].value);
        MsgReply(rcvid, EOK, &reply, sizeof reply);
        break;
    default:
        MsgError(rcvid, EINVAL);
        break;
    }
}

int main(void)
{
    name_attach_t *att = name_attach(NULL, CFGSVC_NAME, 0);
    union {
        uint16_t type;
        struct _pulse pulse;
        struct cfg_msg cfg;
    } msg;

    if (att == NULL) {
        perror("cfgsrv: name_attach");
        return 1;
    }
    printf("cfgsrv: ready\n");
    fflush(stdout);
    for (;;) {
        int rcvid = MsgReceive(att->chid, &msg, sizeof msg, NULL);

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
        if (msg.type != CFG_MSG_TYPE) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        handle(rcvid, &msg.cfg);
    }
}
