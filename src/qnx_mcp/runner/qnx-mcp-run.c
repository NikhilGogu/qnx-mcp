/*
 * qnx-mcp-run: target-side runner for qnx-mcp (QNX OS 8.0).
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Usage: qnx-mcp-run [-t timeout_ms] [-m max_capture_bytes] [-w core_wait_ms]
 *                    [-C cwd] [-e NAME=VALUE]... -- program [args...]
 *
 * Runs one program in its own process group with stdin on /dev/null, captures
 * stdout and stderr separately (each capped at max_capture_bytes), enforces a
 * wall-clock timeout by killing the whole process group, and prints exactly one
 * JSON object describing how the program ended.
 *
 * Crash handling uses the dumper's own client interface instead of guessing
 * file names: before starting the program we register a pulse with
 * DCMD_DUMPER_NOTIFYEVENT (MsgRegisterEvent + an updateable pulse, so dumper
 * writes the dumped pid into the pulse value). If the program dies from a
 * signal that dumper handles, we wait for the pulse carrying its pid and then
 * ask DCMD_DUMPER_GETPATH for the exact core file path.
 */
#include <devctl.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/dcmd_dumper.h>
#include <sys/neutrino.h>
#include <sys/siginfo.h>
#include <sys/wait.h>

#ifndef QMR_VERSION
#define QMR_VERSION "dev"
#endif

#define DUMP_PULSE_CODE (_PULSE_CODE_MINAVAIL + 7)
#define MAX_ENV 64

static volatile sig_atomic_t g_stop_signal;

static void on_stop(int signo) { g_stop_signal = signo; }

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

struct capture {
    int fd;
    int open;
    char *data;
    size_t len;
    size_t cap;
    size_t total;
};

static void capture_read(struct capture *c)
{
    char tmp[8192];
    ssize_t n = read(c->fd, tmp, sizeof tmp);
    if (n > 0) {
        size_t room = c->cap - c->len;
        size_t k = (size_t)n < room ? (size_t)n : room;
        memcpy(c->data + c->len, tmp, k);
        c->len += k;
        c->total += (size_t)n;
    } else if (n == 0 || (errno != EINTR && errno != EAGAIN)) {
        close(c->fd);
        c->open = 0;
    }
}

static void json_string(const char *s, size_t n)
{
    putchar('"');
    for (size_t i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)s[i];
        switch (ch) {
        case '"': fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (ch < 0x20 || ch == 0x7f) {
                printf("\\u%04x", ch);
            } else {
                putchar(ch); /* UTF-8 passes through; the host decodes leniently */
            }
        }
    }
    putchar('"');
}

static void json_cstr_or_null(const char *s)
{
    if (s == NULL) {
        fputs("null", stdout);
    } else {
        json_string(s, strlen(s));
    }
}

static const struct { int no; const char *name; } k_signals[] = {
    {SIGHUP, "SIGHUP"},   {SIGINT, "SIGINT"},   {SIGQUIT, "SIGQUIT"}, {SIGILL, "SIGILL"},
    {SIGTRAP, "SIGTRAP"}, {SIGABRT, "SIGABRT"}, {SIGEMT, "SIGEMT"},   {SIGFPE, "SIGFPE"},
    {SIGKILL, "SIGKILL"}, {SIGBUS, "SIGBUS"},   {SIGSEGV, "SIGSEGV"}, {SIGSYS, "SIGSYS"},
    {SIGPIPE, "SIGPIPE"}, {SIGALRM, "SIGALRM"}, {SIGTERM, "SIGTERM"}, {SIGUSR1, "SIGUSR1"},
    {SIGUSR2, "SIGUSR2"}, {SIGCHLD, "SIGCHLD"}, {SIGPWR, "SIGPWR"},   {SIGWINCH, "SIGWINCH"},
    {SIGURG, "SIGURG"},   {SIGIO, "SIGIO"},     {SIGSTOP, "SIGSTOP"}, {SIGTSTP, "SIGTSTP"},
    {SIGCONT, "SIGCONT"}, {SIGTTIN, "SIGTTIN"}, {SIGTTOU, "SIGTTOU"}, {SIGVTALRM, "SIGVTALRM"},
    {SIGPROF, "SIGPROF"}, {SIGXCPU, "SIGXCPU"}, {SIGXFSZ, "SIGXFSZ"},
};

static const char *signal_name(int signo)
{
    for (size_t i = 0; i < sizeof k_signals / sizeof k_signals[0]; i++) {
        if (k_signals[i].no == signo) {
            return k_signals[i].name;
        }
    }
    return NULL;
}

/* The signals for which dumper writes a core (see the dumper utility docs). */
static int dumper_signal(int signo)
{
    switch (signo) {
    case SIGABRT: case SIGBUS: case SIGEMT: case SIGFPE: case SIGILL:
    case SIGQUIT: case SIGSEGV: case SIGSYS: case SIGTRAP: case SIGXCPU:
        return 1;
    default:
        return 0;
    }
}

static void usage_error(const char *msg)
{
    printf("{\"runner\":\"qnx-mcp-run\",\"version\":\"%s\",\"usage_error\":", QMR_VERSION);
    json_cstr_or_null(msg);
    puts("}");
    exit(2);
}

int main(int argc, char *argv[])
{
    long timeout_ms = 30000;
    long core_wait_ms = 10000;
    size_t max_bytes = 65536;
    const char *cwd = NULL;
    char *envs[MAX_ENV];
    int nenv = 0;
    int opt;

    while ((opt = getopt(argc, argv, "t:m:w:C:e:V")) != -1) {
        switch (opt) {
        case 't': timeout_ms = strtol(optarg, NULL, 10); break;
        case 'm': max_bytes = (size_t)strtoul(optarg, NULL, 10); break;
        case 'w': core_wait_ms = strtol(optarg, NULL, 10); break;
        case 'C': cwd = optarg; break;
        case 'e':
            if (nenv == MAX_ENV || strchr(optarg, '=') == NULL) {
                usage_error("bad -e NAME=VALUE");
            }
            envs[nenv++] = optarg;
            break;
        case 'V': printf("qnx-mcp-run %s\n", QMR_VERSION); return 0;
        default: usage_error("unknown option");
        }
    }
    if (optind >= argc) {
        usage_error("missing program");
    }
    if (timeout_ms <= 0 || max_bytes > (64u << 20)) {
        usage_error("bad -t or -m value");
    }
    char **prog = &argv[optind];

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    /* Register for dump notifications before the program can crash. */
    const char *dumper_state = "unavailable";
    int chid = ChannelCreate(_NTO_CHF_FIXED_PRIORITY);
    int coid = -1;
    int dfd = -1;
    if (chid != -1) {
        coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, _NTO_COF_CLOEXEC);
    }
    if (coid != -1) {
        dfd = open("/proc/dumper", O_RDONLY | O_CLOEXEC);
    }
    if (dfd != -1) {
        struct sigevent ev;
        SIGEV_PULSE_INIT(&ev, coid, sched_get_priority_max(SCHED_RR), DUMP_PULSE_CODE, -1);
        SIGEV_MAKE_UPDATEABLE(&ev);
        if (MsgRegisterEvent(&ev, dfd) == -1 ||
            devctl(dfd, DCMD_DUMPER_NOTIFYEVENT, &ev, sizeof ev, NULL) != EOK) {
            close(dfd);
            dfd = -1;
        } else {
            dumper_state = "registered";
        }
    }

    int outp[2], errp[2], execp[2];
    if (pipe(outp) == -1 || pipe(errp) == -1 || pipe(execp) == -1) {
        usage_error("pipe failed");
    }
    fcntl(execp[1], F_SETFD, FD_CLOEXEC);

    uint64_t start = now_ms();
    pid_t pid = fork();
    if (pid == -1) {
        usage_error("fork failed");
    }
    if (pid == 0) {
        setpgid(0, 0);
        signal(SIGPIPE, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        signal(SIGTERM, SIG_DFL);
        signal(SIGINT, SIG_DFL);
        int devnull = open("/dev/null", O_RDONLY);
        if (devnull != -1) {
            dup2(devnull, 0);
        }
        dup2(outp[1], 1);
        dup2(errp[1], 2);
        close(outp[0]); close(outp[1]); close(errp[0]); close(errp[1]); close(execp[0]);
        for (int i = 0; i < nenv; i++) {
            putenv(envs[i]);
        }
        if (cwd != NULL && chdir(cwd) != 0) {
            int e = errno;
            (void)!write(execp[1], &e, sizeof e);
            _exit(127);
        }
        execvp(prog[0], prog);
        int e = errno;
        (void)!write(execp[1], &e, sizeof e);
        _exit(127);
    }
    setpgid(pid, pid);
    close(outp[1]);
    close(errp[1]);
    close(execp[1]);

    int exec_errno = 0;
    if (read(execp[0], &exec_errno, sizeof exec_errno) != (ssize_t)sizeof exec_errno) {
        exec_errno = 0; /* EOF: exec succeeded (close-on-exec) */
    }
    close(execp[0]);

    struct capture out = {outp[0], 1, malloc(max_bytes + 1), 0, max_bytes, 0};
    struct capture err = {errp[0], 1, malloc(max_bytes + 1), 0, max_bytes, 0};
    if (out.data == NULL || err.data == NULL) {
        kill(-pid, SIGKILL);
        usage_error("out of memory");
    }

    int status = 0;
    int reaped = 0;
    int timed_out = 0;
    int stopped = 0;
    uint64_t reaped_at = 0;
    uint64_t deadline = start + (uint64_t)timeout_ms;

    while (!reaped || out.open || err.open) {
        uint64_t now = now_ms();
        if (!reaped && !timed_out && now >= deadline) {
            kill(-pid, SIGKILL);
            timed_out = 1;
        }
        if (!reaped && g_stop_signal && !stopped) {
            kill(-pid, SIGKILL);
            stopped = g_stop_signal;
        }
        if (!reaped) {
            pid_t r = waitpid(pid, &status, WNOHANG);
            if (r == pid) {
                reaped = 1;
                reaped_at = now;
            }
        }
        /* Descendants may keep the pipes open after the program exits. */
        if (reaped && (out.open || err.open) && now - reaped_at > 300) {
            if (out.open) { close(out.fd); out.open = 0; }
            if (err.open) { close(err.fd); err.open = 0; }
            break;
        }
        struct pollfd fds[2];
        int nfds = 0;
        if (out.open) { fds[nfds].fd = out.fd; fds[nfds].events = POLLIN; fds[nfds].revents = 0; nfds++; }
        if (err.open) { fds[nfds].fd = err.fd; fds[nfds].events = POLLIN; fds[nfds].revents = 0; nfds++; }
        if (nfds == 0) {
            if (!reaped) {
                usleep(20000);
            }
            continue;
        }
        if (poll(fds, (nfds_t)nfds, 20) > 0) {
            for (int i = 0; i < nfds; i++) {
                if (fds[i].revents == 0) {
                    continue;
                }
                if (out.open && fds[i].fd == out.fd) {
                    capture_read(&out);
                } else if (err.open && fds[i].fd == err.fd) {
                    capture_read(&err);
                }
            }
        }
    }
    uint64_t duration = now_ms() - start;

    int exited = WIFEXITED(status) && exec_errno == 0;
    int signaled = WIFSIGNALED(status);
    int signo = signaled ? WTERMSIG(status) : 0;
    int core_expected = signaled && dumper_signal(signo);

    char core_path[PATH_MAX];
    int have_core = 0;
    if (!core_expected) {
        if (dfd != -1) {
            dumper_state = "not_applicable";
        }
    } else if (dfd != -1) {
        dumper_state = "timeout";
        uint64_t wait_deadline = now_ms() + (uint64_t)(core_wait_ms > 0 ? core_wait_ms : 0);
        for (;;) {
            uint64_t t = now_ms();
            if (t >= wait_deadline) {
                break;
            }
            uint64_t ns = (wait_deadline - t) * 1000000ull;
            struct _pulse pulse;
            TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, NULL, &ns, NULL);
            if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == -1) {
                if (errno == EINTR) {
                    continue;
                }
                break;
            }
            if (pulse.code == DUMP_PULSE_CODE && pulse.value.sival_int == pid) {
                dump_info_t info;
                memset(&info, 0, sizeof info);
                info.i.pid = pid;
                if (devctl(dfd, DCMD_DUMPER_GETPATH, &info, sizeof info, NULL) == EOK) {
                    strncpy(core_path, info.o.dump_pathname, sizeof core_path - 1);
                    core_path[sizeof core_path - 1] = '\0';
                    have_core = core_path[0] != '\0';
                }
                dumper_state = have_core ? "notified" : "notified_no_path";
                break;
            }
        }
    }
    if (dfd != -1) {
        devctl(dfd, DCMD_DUMPER_REMOVEEVENT, NULL, 0, NULL);
        close(dfd);
    }

    printf("{\"runner\":\"qnx-mcp-run\",\"version\":\"%s\",\"pid\":%d,", QMR_VERSION, (int)pid);
    fputs("\"exec_error\":", stdout);
    json_cstr_or_null(exec_errno ? strerror(exec_errno) : NULL);
    printf(",\"exited\":%s,\"exit_code\":", exited ? "true" : "false");
    if (exited) {
        printf("%d", WEXITSTATUS(status));
    } else {
        fputs("null", stdout);
    }
    printf(",\"signaled\":%s,\"signal\":", signaled ? "true" : "false");
    if (signaled) {
        printf("%d", signo);
    } else {
        fputs("null", stdout);
    }
    fputs(",\"signal_name\":", stdout);
    json_cstr_or_null(signaled ? signal_name(signo) : NULL);
    printf(",\"core_expected\":%s,\"timed_out\":%s,\"runner_stopped_by\":",
           core_expected ? "true" : "false", timed_out ? "true" : "false");
    json_cstr_or_null(stopped ? signal_name(stopped) : NULL);
    printf(",\"duration_ms\":%llu,\"stdout\":", (unsigned long long)duration);
    json_string(out.data, out.len);
    printf(",\"stdout_bytes\":%zu,\"stdout_truncated\":%s,\"stderr\":", out.total,
           out.total > out.len ? "true" : "false");
    json_string(err.data, err.len);
    printf(",\"stderr_bytes\":%zu,\"stderr_truncated\":%s,\"dumper\":\"%s\",\"core_path\":", err.total,
           err.total > err.len ? "true" : "false", dumper_state);
    json_cstr_or_null(have_core ? core_path : NULL);
    puts("}");
    return 0;
}
