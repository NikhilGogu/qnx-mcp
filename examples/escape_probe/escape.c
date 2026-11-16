/*
 * escape_probe: tries things a program started by qnx-mcp with QNX_MCP_RUN_AS should not be able to do,
 * and prints one JSON line per attempt. tests/test_nonroot_e2e.py runs it on a target and checks that
 * every attempt failed. Every attempt is harmless if it succeeds: signals are probes (signal 0), files it
 * creates are removed again, and memory it maps is only unmapped.
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Usage: escape [--other-run DIR] [--escape]
 *   --other-run DIR  also try to list DIR and read DIR/stdout (another run's directory)
 *   --escape         fork a child that calls setsid() and outlives the run (prints its pid), then exit
 *
 * NOT VERIFIED ON A TARGET: the QNX-specific attempts (ThreadCtl(_NTO_TCTL_IO), mmap_device_memory, the
 * /proc paths) follow the QNX OS 8.0 documentation; which errno each returns for a non-root process is
 * what the end-to-end test records.
 */
#include <errno.h>
#include <fcntl.h>
#include <dirent.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#ifdef __QNX__
#include <sys/neutrino.h>
#endif

static const char *errno_name(int e)
{
    switch (e) {
    case 0: return "OK";
    case EPERM: return "EPERM";
    case EACCES: return "EACCES";
    case EROFS: return "EROFS";
    case ENOENT: return "ENOENT";
    case ENOSYS: return "ENOSYS";
    case EINVAL: return "EINVAL";
    case ESRCH: return "ESRCH";
    case EBUSY: return "EBUSY";
    case ENOTSUP: return "ENOTSUP";
    default: return "OTHER";
    }
}

static void report(const char *attempt, int ok, int e)
{
    printf("{\"attempt\":\"%s\",\"succeeded\":%s,\"errno\":%d,\"errno_name\":\"%s\"}\n", attempt, ok ? "true" : "false",
           ok ? 0 : e, ok ? "OK" : errno_name(e));
    fflush(stdout);
}

static void try_create(const char *attempt, const char *path)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    int e = errno;
    if (fd != -1) {
        close(fd);
        unlink(path);
    }
    report(attempt, fd != -1, e);
}

int main(int argc, char *argv[])
{
    const char *other = NULL;
    int escape = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--other-run") == 0 && i + 1 < argc) {
            other = argv[++i];
        } else if (strcmp(argv[i], "--escape") == 0) {
            escape = 1;
        }
    }
    printf("{\"attempt\":\"identity\",\"uid\":%d,\"euid\":%d,\"gid\":%d,\"egid\":%d}\n", (int)getuid(), (int)geteuid(), (int)getgid(),
           (int)getegid());
    fflush(stdout);

    if (escape) {
        pid_t pid = fork();
        if (pid == 0) {
            setsid();
            for (int i = 0; i < 120; i++) {
                sleep(1);
            }
            _exit(0);
        }
        printf("{\"attempt\":\"escape\",\"child\":%d}\n", (int)pid);
        return pid > 0 ? 0 : 1;
    }

    int rc = kill(1, 0); /* signal 0: permission check only */
    report("signal_procnto", rc == 0, errno);
    rc = kill(getppid(), 0); /* the runner, which keeps the login user's identity */
    report("signal_runner", rc == 0, errno);

    int fd = open("/proc/1/as", O_RDWR);
    int e = errno;
    if (fd != -1) {
        close(fd);
    }
    report("open_procnto_as_rw", fd != -1, e);

    try_create("create_in_proc_boot", "/proc/boot/qnx-mcp-escape-probe");
    try_create("create_in_system_bin", "/system/bin/qnx-mcp-escape-probe");
    try_create("create_in_etc", "/etc/qnx-mcp-escape-probe");

    rc = setuid(0);
    report("setuid_0", rc == 0, errno);

#ifdef __QNX__
    rc = ThreadCtl(_NTO_TCTL_IO, 0);
    report("threadctl_io", rc != -1, errno);
    void *m = mmap_device_memory(NULL, 4096, PROT_READ | PROT_NOCACHE, 0, 0xA0000);
    e = errno;
    if (m != MAP_FAILED) {
        munmap_device_memory(m, 4096);
    }
    report("mmap_device_memory", m != MAP_FAILED, e);
#endif

    if (other != NULL) {
        DIR *d = opendir(other);
        e = errno;
        if (d != NULL) {
            closedir(d);
        }
        report("list_other_run", d != NULL, e);
        char p[1024];
        snprintf(p, sizeof p, "%s/stdout", other);
        fd = open(p, O_RDONLY);
        e = errno;
        if (fd != -1) {
            close(fd);
        }
        report("read_other_run_output", fd != -1, e);
    }
    return 0;
}
