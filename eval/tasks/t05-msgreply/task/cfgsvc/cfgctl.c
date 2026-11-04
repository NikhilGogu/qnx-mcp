/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "cfgsvc.h"

int main(int argc, char **argv)
{
    struct cfg_msg msg = { .type = CFG_MSG_TYPE };
    struct cfg_reply reply;
    int coid, get;

    get = argc == 3 && strcmp(argv[1], "get") == 0;
    if (!get && !(argc == 4 && strcmp(argv[1], "set") == 0)) {
        fprintf(stderr, "usage: cfgctl get <key> | cfgctl set <key> <value>\n");
        return 1;
    }
    msg.op = get ? CFG_GET : CFG_SET;
    snprintf(msg.key, sizeof msg.key, "%s", argv[2]);
    if (!get)
        snprintf(msg.value, sizeof msg.value, "%s", argv[3]);
    coid = name_open(CFGSVC_NAME, 0);
    if (coid == -1) {
        perror("cfgctl: name_open");
        return 1;
    }
    if (MsgSend(coid, &msg, sizeof msg, &reply, sizeof reply) == -1) {
        int err = errno;

        name_close(coid);
        if (err == ENOENT) {
            fprintf(stderr, "cfgctl: no such key: %s\n", msg.key);
            return 2;
        }
        fprintf(stderr, "cfgctl: %s\n", strerror(err));
        return 1;
    }
    name_close(coid);
    reply.value[sizeof reply.value - 1] = '\0';
    printf("%s=%s\n", msg.key, reply.value);
    return 0;
}
