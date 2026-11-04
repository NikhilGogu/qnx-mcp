/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <sys/resource.h>
#include "parsers.h"
#include "status.h"

static const char *state_dir = "/data/var/status";
static unsigned long served, failed;

static int valid_component(const char *s)
{
    if (*s == '\0')
        return 0;
    for (; *s; s++)
        if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '_' || *s == '-'))
            return 0;
    return 1;
}

/* Components that keep versioned state files point <component>.state at the
 * current version; the answer then names that file. */
static void link_note(const char *path, char *note, size_t n)
{
    char target[96];
    ssize_t len = readlink(path, target, sizeof target - 1);
    FILE *f;

    note[0] = '\0';
    if (len <= 0)
        return;
    target[len] = '\0';
    f = fopen(path, "r");
    if (f == NULL) {
        snprintf(note, n, " [%s, unreadable]", target);
        return;
    }
    snprintf(note, n, " [%s]", target);
}

static int lookup(const char *component, struct status_reply *rep)
{
    char path[256];
    struct comp_state cs;
    int format, err;

    if (!valid_component(component))
        return EINVAL;
    snprintf(path, sizeof path, "%s/%s.state", state_dir, component);
    if ((err = detect_format(path, &format)) != 0)
        return err;
    switch (format) {
    case FMT_SECTIONS:
        err = parse_sections(path, &cs);
        break;
    case FMT_LEGACY:
        err = parse_legacy(path, &cs);
        break;
    default:
        err = parse_kv(path, &cs);
        break;
    }
    if (err == 0) {
        char note[112];

        link_note(path, note, sizeof note);
        snprintf(rep->text, sizeof rep->text, "%s: %s since %ld code %d%s", component, cs.state, cs.since, cs.code, note);
    }
    return err;
}

int main(int argc, char **argv)
{
    struct rlimit rl = { 48, 48 };
    name_attach_t *att;
    int opt;
    union {
        uint16_t type;
        struct _pulse pulse;
        struct status_msg req;
    } msg;

    while ((opt = getopt(argc, argv, "d:")) != -1)
        if (opt == 'd')
            state_dir = optarg;
    setrlimit(RLIMIT_NOFILE, &rl);
    att = name_attach(NULL, STATUS_NAME, 0);
    if (att == NULL) {
        perror("statusd: name_attach");
        return 1;
    }
    fprintf(stderr, "statusd: serving %s\n", state_dir);
    for (;;) {
        int rcvid = MsgReceive(att->chid, &msg, sizeof msg, NULL);
        struct status_reply rep;

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
        if (msg.type != MSG_STATUS) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        msg.req.component[sizeof msg.req.component - 1] = '\0';
        memset(&rep, 0, sizeof rep);
        rep.err = lookup(msg.req.component, &rep);
        if (rep.err == 0)
            served++;
        else
            failed++;
        MsgReply(rcvid, EOK, &rep, sizeof rep);
    }
}
