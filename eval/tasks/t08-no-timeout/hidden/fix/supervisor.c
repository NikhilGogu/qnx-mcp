/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/neutrino.h>
#include <sys/wait.h>
#include "ready.h"

extern char **environ;

int main(int argc, char **argv)
{
    char *config = argc > 1 ? argv[1] : "worker.conf";
    char pidbuf[16], chidbuf[16];
    char *wargv[] = { "./worker", pidbuf, chidbuf, config, NULL };
    struct _pulse pulse;
    struct timespec deadline;
    pid_t worker;
    int chid, rc, status;

    chid = ChannelCreate(0);
    if (chid == -1) {
        perror("supervisor: ChannelCreate");
        return 1;
    }
    snprintf(pidbuf, sizeof pidbuf, "%d", getpid());
    snprintf(chidbuf, sizeof chidbuf, "%d", chid);
    rc = posix_spawn(&worker, "./worker", NULL, NULL, wargv, environ);
    if (rc != 0) {
        fprintf(stderr, "supervisor: cannot start worker: %s\n", strerror(rc));
        return 1;
    }
    printf("supervisor: started worker %d\n", worker);
    fflush(stdout);
    clock_gettime(CLOCK_MONOTONIC, &deadline);
    deadline.tv_sec += 2;
    for (;;) {
        struct timespec now;
        long long left;
        uint64_t ns;

        clock_gettime(CLOCK_MONOTONIC, &now);
        left = (deadline.tv_sec - now.tv_sec) * 1000000000LL + (deadline.tv_nsec - now.tv_nsec);
        ns = left > 0 ? (uint64_t)left : 1;
        TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, NULL, &ns, NULL);
        if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == -1) {
            if (errno == ETIMEDOUT) {
                printf("supervisor: worker not ready after 2 s\n");
                fflush(stdout);
                kill(worker, SIGKILL);
                waitpid(worker, &status, 0);
                return 3;
            }
            perror("supervisor: MsgReceivePulse");
            return 1;
        }
        if (pulse.code == PULSE_READY)
            break;
    }
    printf("supervisor: worker ready\n");
    fflush(stdout);
    if (waitpid(worker, &status, 0) == -1) {
        perror("supervisor: waitpid");
        return 1;
    }
    printf("supervisor: worker exited with status %d\n", WIFEXITED(status) ? WEXITSTATUS(status) : -1);
    return 0;
}
