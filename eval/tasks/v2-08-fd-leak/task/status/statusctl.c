/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include "status.h"

int main(int argc, char **argv)
{
    struct status_msg req = { .type = MSG_STATUS };
    struct status_reply rep;
    int coid;

    if (argc != 2) {
        fprintf(stderr, "usage: statusctl <component>\n");
        return 1;
    }
    snprintf(req.component, sizeof req.component, "%s", argv[1]);
    coid = name_open(STATUS_NAME, 0);
    if (coid == -1 || MsgSend(coid, &req, sizeof req, &rep, sizeof rep) == -1) {
        fprintf(stderr, "statusctl: statusd: %s\n", strerror(errno));
        return 1;
    }
    name_close(coid);
    switch (rep.err) {
    case 0:
        printf("%s\n", rep.text);
        return 0;
    case ENOENT:
        fprintf(stderr, "statusctl: %s: no such component\n", argv[1]);
        return 2;
    case EBADMSG:
        fprintf(stderr, "statusctl: %s: malformed state file\n", argv[1]);
        return 3;
    default:
        fprintf(stderr, "statusctl: %s: internal error (%s)\n", argv[1], strerror(rep.err));
        return 1;
    }
}
