/*
 * qnx-mcp-run: target-side runner for qnx-mcp (QNX OS 8.0).
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Modes, one per invocation; every mode prints exactly one JSON object on stdout:
 *   qnx-mcp-run [run options] -- program [args...]     run once and report how it ended
 *   qnx-mcp-run -B dir [run options] -- program [args]  start in the background (own session);
 *       output goes to dir/stdout and dir/stderr, the final report to dir/status.json
 *   qnx-mcp-run -P dir -O out_off -E err_off -L max     poll a background run (output from cursors)
 *   qnx-mcp-run -K dir [-w wait_ms]                     stop a background run
 *   qnx-mcp-run -H file                                 size, mode and SHA-256 of a regular file
 *   qnx-mcp-run -V                                      print the version
 * Run options: -t timeout_ms, -m max bytes kept per stream, -w core_wait_ms, -C cwd,
 *   -e NAME=VALUE (repeatable), -R dir (move the core file there), -S (stop the program
 *   when stdin reaches EOF, which is how the ssh client going away shows up here).
 *
 * The program runs in its own process group with stdin on /dev/null. When it exits, or at
 * the deadline, the whole group is killed, so descendants that stayed in the group do not
 * outlive the run (a descendant that calls setsid() escapes; that is not containable here).
 *
 * Crash handling uses the dumper's client interface instead of guessing file names: before
 * the program starts we register an updateable pulse with DCMD_DUMPER_NOTIFYEVENT, so dumper
 * writes the dumped pid into the pulse value. If the program dies from a signal that dumper
 * handles, we wait (bounded) for the pulse carrying its pid and ask DCMD_DUMPER_GETPATH for
 * the exact core path. Every call to dumper is bounded with TimerTimeout.
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
#include <sys/stat.h>
#include <sys/wait.h>

#ifndef QMR_VERSION
#define QMR_VERSION "dev"
#endif

#define DUMP_PULSE_CODE (_PULSE_CODE_MINAVAIL + 7)
#define MAX_ENV 64
#define MAX_WARN 16
#define DUMPER_CALL_NS 2000000000ull
#define PIPE_GRACE_MS 300
#define GROUP_GRACE_MS 2000

static volatile sig_atomic_t g_stop_signal;

static void on_stop(int signo) { g_stop_signal = signo; }

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

/* ------------------------------------------------------------------ warnings */

static const char *g_warn[MAX_WARN];
static int g_warn_errno[MAX_WARN];
static int g_nwarn;

static void warn_errno(const char *what, int e)
{
    if (g_nwarn < MAX_WARN) {
        g_warn[g_nwarn] = what;
        g_warn_errno[g_nwarn] = e;
        g_nwarn++;
    }
}

/* ------------------------------------------------------------------ JSON output */

static void j_str(FILE *o, const char *s, size_t n)
{
    fputc('"', o);
    for (size_t i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)s[i];
        switch (ch) {
        case '"': fputs("\\\"", o); break;
        case '\\': fputs("\\\\", o); break;
        case '\n': fputs("\\n", o); break;
        case '\r': fputs("\\r", o); break;
        case '\t': fputs("\\t", o); break;
        default:
            if (ch < 0x20 || ch == 0x7f) {
                fprintf(o, "\\u%04x", ch);
            } else {
                fputc(ch, o); /* UTF-8 passes through; the host decodes leniently */
            }
        }
    }
    fputc('"', o);
}

static void j_cstr(FILE *o, const char *s)
{
    if (s == NULL) {
        fputs("null", o);
    } else {
        j_str(o, s, strlen(s));
    }
}

static void j_head(FILE *o)
{
    fprintf(o, "{\"runner\":\"qnx-mcp-run\",\"version\":\"%s\"", QMR_VERSION);
}

static void j_warnings(FILE *o)
{
    fputs(",\"warnings\":[", o);
    for (int i = 0; i < g_nwarn; i++) {
        fprintf(o, "%s{\"call\":", i ? "," : "");
        j_cstr(o, g_warn[i]);
        fputs(",\"error\":", o);
        j_cstr(o, strerror(g_warn_errno[i]));
        fputc('}', o);
    }
    fputc(']', o);
}

static void fail(const char *what, int e)
{
    j_head(stdout);
    fputs(",\"usage_error\":", stdout);
    j_cstr(stdout, what);
    fputs(",\"error\":", stdout);
    j_cstr(stdout, e ? strerror(e) : NULL);
    puts("}");
    fflush(stdout);
    exit(2);
}

/* ------------------------------------------------------------------ SHA-256 */

static const uint32_t k_sha[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

struct sha256 {
    uint32_t h[8];
    uint64_t len;
    uint8_t buf[64];
    size_t n;
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_block(struct sha256 *c, const uint8_t *p)
{
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = c->h[0], b = c->h[1], cc = c->h[2], d = c->h[3];
    uint32_t e = c->h[4], f = c->h[5], g = c->h[6], h = c->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + k_sha[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & cc) ^ (b & cc));
        h = g; g = f; f = e; e = d + t1; d = cc; cc = b; b = a; a = t1 + t2;
    }
    c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d;
    c->h[4] += e; c->h[5] += f; c->h[6] += g; c->h[7] += h;
}

static void sha256_init(struct sha256 *c)
{
    static const uint32_t iv[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(c->h, iv, sizeof iv);
    c->len = 0;
    c->n = 0;
}

static void sha256_update(struct sha256 *c, const uint8_t *p, size_t n)
{
    c->len += n;
    while (n > 0) {
        size_t k = 64 - c->n;
        if (k > n) {
            k = n;
        }
        memcpy(c->buf + c->n, p, k);
        c->n += k;
        p += k;
        n -= k;
        if (c->n == 64) {
            sha256_block(c, c->buf);
            c->n = 0;
        }
    }
}

static void sha256_hex(struct sha256 *c, char out[65])
{
    uint64_t bits = c->len * 8;
    uint8_t pad = 0x80, zero = 0, lb[8];
    sha256_update(c, &pad, 1);
    while (c->n != 56) {
        sha256_update(c, &zero, 1);
    }
    for (int i = 0; i < 8; i++) {
        lb[i] = (uint8_t)(bits >> (56 - 8 * i));
    }
    sha256_update(c, lb, 8);
    for (int i = 0; i < 8; i++) {
        snprintf(out + 8 * i, 9, "%08x", (unsigned)c->h[i]);
    }
}

static int mode_hash(const char *path)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    struct stat st;
    if (fd == -1 || fstat(fd, &st) == -1) {
        fail("open", errno);
    }
    if (!S_ISREG(st.st_mode)) {
        fail("not a regular file", 0);
    }
    struct sha256 c;
    sha256_init(&c);
    uint8_t buf[65536];
    for (;;) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n == 0) {
            break;
        }
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fail("read", errno);
        }
        sha256_update(&c, buf, (size_t)n);
    }
    close(fd);
    uint64_t size = c.len; /* before sha256_hex adds the padding */
    char hex[65];
    sha256_hex(&c, hex);
    j_head(stdout);
    fputs(",\"path\":", stdout);
    j_cstr(stdout, path);
    printf(",\"size\":%llu,\"mode\":\"%04o\",\"sha256\":\"%s\"}\n", (unsigned long long)size, (unsigned)(st.st_mode & 07777), hex);
    return 0;
}

/* ------------------------------------------------------------------ signals */

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

/* ------------------------------------------------------------------ dumper */

struct dumper {
    int chid;
    int coid;
    int fd;
    const char *state;
    int err;
};

/* Bound the next blocking kernel call (devctl is a single MsgSend to dumper). */
static void bound_next_call(int flags, uint64_t ns)
{
    uint64_t t = ns;
    if (TimerTimeout(CLOCK_MONOTONIC, flags, NULL, &t, NULL) == -1) {
        warn_errno("TimerTimeout", errno);
    }
}

static void dumper_register(struct dumper *d)
{
    d->chid = d->coid = d->fd = -1;
    d->state = "unavailable";
    d->err = 0;
    d->chid = ChannelCreate(_NTO_CHF_FIXED_PRIORITY);
    if (d->chid == -1) {
        d->err = errno;
        return;
    }
    d->coid = ConnectAttach(0, 0, d->chid, _NTO_SIDE_CHANNEL, _NTO_COF_CLOEXEC);
    if (d->coid == -1) {
        d->err = errno;
        return;
    }
    d->fd = open("/proc/dumper", O_RDONLY | O_CLOEXEC);
    if (d->fd == -1) {
        d->err = errno;
        return;
    }
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, d->coid, sched_get_priority_max(SCHED_RR), DUMP_PULSE_CODE, -1);
    SIGEV_MAKE_UPDATEABLE(&ev);
    int rc = MsgRegisterEvent(&ev, d->fd) == -1 ? errno : EOK;
    if (rc == EOK) {
        bound_next_call(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, DUMPER_CALL_NS);
        rc = devctl(d->fd, DCMD_DUMPER_NOTIFYEVENT, &ev, sizeof ev, NULL);
    }
    if (rc != EOK) {
        d->err = rc;
        close(d->fd);
        d->fd = -1;
        return;
    }
    d->state = "registered";
}

static void dumper_unregister(struct dumper *d)
{
    if (d->fd != -1) {
        bound_next_call(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, DUMPER_CALL_NS);
        int rc = devctl(d->fd, DCMD_DUMPER_REMOVEEVENT, NULL, 0, NULL);
        if (rc != EOK) {
            warn_errno("DCMD_DUMPER_REMOVEEVENT", rc);
        }
        close(d->fd);
        d->fd = -1;
    }
}

/* ------------------------------------------------------------------ run */

struct opts {
    long timeout_ms;
    long core_wait_ms;
    size_t max_bytes;
    const char *cwd;
    const char *core_dir;
    char *envs[MAX_ENV];
    int nenv;
    int watch_stdin;
    char **prog;
};

struct sink {
    int fd;     /* pipe read end, -1 once closed */
    char *buf;  /* foreground: memory buffer of cap bytes */
    int file;   /* background: output file, -1 if none */
    size_t cap;
    size_t kept;
    uint64_t total;
    int eof;
};

struct result {
    pid_t pid;
    int exec_errno;
    int exited, exit_code, signaled, signo;
    int timed_out, stopped_by, disconnected;
    int group_clean;
    int capture_complete;
    uint64_t duration_ms;
    uint64_t exit_at;       /* monotonic ms when the program exited */
    long core_ready_ms;     /* from the exit to dumper's completion pulse, -1 if none */
    const char *dumper_state;
    int dumper_errno;
    int have_core;
    char core_path[PATH_MAX];
    char dumper_core_path[PATH_MAX];
    const char *core_move_error;
};

static void sink_read(struct sink *s)
{
    char tmp[8192];
    ssize_t n = read(s->fd, tmp, sizeof tmp);
    if (n > 0) {
        size_t room = s->cap - s->kept;
        size_t k = (size_t)n < room ? (size_t)n : room;
        if (k > 0 && s->buf != NULL) {
            memcpy(s->buf + s->kept, tmp, k);
        } else if (k > 0 && s->file != -1) {
            size_t off = 0;
            while (off < k) {
                ssize_t w = write(s->file, tmp + off, k - off);
                if (w < 0) {
                    if (errno == EINTR) {
                        continue;
                    }
                    warn_errno("write output file", errno);
                    break;
                }
                off += (size_t)w;
            }
            k = off;
        }
        s->kept += k;
        s->total += (uint64_t)n;
    } else if (n == 0) {
        close(s->fd);
        s->fd = -1;
        s->eof = 1;
    } else if (errno != EINTR && errno != EAGAIN) {
        warn_errno("read output pipe", errno);
        close(s->fd);
        s->fd = -1;
    }
}

static void kill_group(pid_t pid, int sig)
{
    /* Only used while the child is not yet reaped, so pid cannot have been reused. */
    if (kill(-pid, sig) == -1) {
        if (errno != ESRCH) {
            warn_errno("kill(process group)", errno);
        }
        if (kill(pid, sig) == -1 && errno != ESRCH) {
            warn_errno("kill(child)", errno);
        }
    }
}

/* After the group leader is reaped: kill what is left of the group and wait until it is empty. */
static int empty_group(pid_t pgid)
{
    uint64_t end = now_ms() + GROUP_GRACE_MS;
    for (;;) {
        if (kill(-pgid, SIGKILL) == -1 && errno == ESRCH) {
            return 1;
        }
        if (now_ms() >= end) {
            return 0;
        }
        usleep(10000);
    }
}

static pid_t start_child(const struct opts *o, int *out_rd, int *err_rd, int *exec_rd)
{
    int outp[2], errp[2], execp[2];
    if (pipe(outp) == -1 || pipe(errp) == -1 || pipe(execp) == -1) {
        fail("pipe", errno);
    }
    if (fcntl(execp[1], F_SETFD, FD_CLOEXEC) == -1 || fcntl(outp[0], F_SETFD, FD_CLOEXEC) == -1 ||
        fcntl(errp[0], F_SETFD, FD_CLOEXEC) == -1 || fcntl(execp[0], F_SETFD, FD_CLOEXEC) == -1) {
        fail("fcntl", errno);
    }
    pid_t pid = fork();
    if (pid == -1) {
        fail("fork", errno);
    }
    if (pid == 0) {
        int e = 0;
        if (setpgid(0, 0) == -1) {
            e = errno;
        }
        signal(SIGPIPE, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
        signal(SIGTERM, SIG_DFL);
        signal(SIGINT, SIG_DFL);
        sigset_t none;
        sigemptyset(&none);
        sigprocmask(SIG_SETMASK, &none, NULL);
        if (e == 0) {
            int dn = open("/dev/null", O_RDONLY);
            if (dn == -1 || dup2(dn, 0) == -1) {
                e = errno;
            } else if (dn != 0) {
                close(dn);
            }
        }
        if (e == 0 && (dup2(outp[1], 1) == -1 || dup2(errp[1], 2) == -1)) {
            e = errno;
        }
        if (e == 0) {
            close(outp[1]);
            close(errp[1]);
        }
        for (int i = 0; e == 0 && i < o->nenv; i++) {
            if (putenv(o->envs[i]) != 0) {
                e = errno;
            }
        }
        if (e == 0 && o->cwd != NULL && chdir(o->cwd) != 0) {
            e = errno;
        }
        if (e == 0) {
            execvp(o->prog[0], o->prog);
            e = errno;
        }
        (void)!write(execp[1], &e, sizeof e);
        _exit(127);
    }
    /* Both sides call setpgid so the group exists before either continues; EACCES means the
     * child already exec'd (and so already ran its own setpgid), ESRCH that it already exited. */
    if (setpgid(pid, pid) == -1 && errno != EACCES && errno != ESRCH) {
        warn_errno("setpgid", errno);
    }
    close(outp[1]);
    close(errp[1]);
    close(execp[1]);
    *out_rd = outp[0];
    *err_rd = errp[0];
    *exec_rd = execp[0];
    return pid;
}

static void record_status(struct result *r, int status)
{
    r->exited = WIFEXITED(status) && r->exec_errno == 0;
    r->exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 0;
    r->signaled = WIFSIGNALED(status);
    r->signo = r->signaled ? WTERMSIG(status) : 0;
}

/* Reap the group leader (it has exited), then empty its process group. */
static void reap(struct result *r, int use_waitid)
{
    if (use_waitid) {
        kill(-r->pid, SIGKILL); /* the zombie still holds the group id, so it cannot be reused */
    }
    int status = 0;
    for (;;) {
        pid_t w = waitpid(r->pid, &status, 0);
        if (w == r->pid) {
            break;
        }
        if (w == -1 && errno == EINTR) {
            continue;
        }
        warn_errno("waitpid", errno);
        break;
    }
    record_status(r, status);
    r->group_clean = empty_group(r->pid);
}

static void supervise(const struct opts *o, struct result *r, struct sink *out, struct sink *err, int exec_rd, uint64_t start)
{
    uint64_t deadline = start + (uint64_t)o->timeout_ms;
    uint64_t done_at = 0;
    int done = 0, killed = 0, use_waitid = 1;
    int stdin_open = o->watch_stdin;
    char exec_buf[sizeof(int)];
    size_t exec_got = 0;

    for (;;) {
        uint64_t now = now_ms();
        if (!done) {
            if (!killed && now >= deadline) {
                kill_group(r->pid, SIGKILL);
                r->timed_out = killed = 1;
            } else if (!killed && g_stop_signal) {
                kill_group(r->pid, SIGKILL);
                r->stopped_by = g_stop_signal;
                killed = 1;
            }
            int exited = 0;
            if (use_waitid) {
                siginfo_t si;
                memset(&si, 0, sizeof si);
                if (waitid(P_PID, (id_t)r->pid, &si, WEXITED | WNOHANG | WNOWAIT) == 0) {
                    exited = si.si_pid == r->pid;
                } else if (errno != EINTR) {
                    if (errno != EINVAL && errno != ENOSYS) {
                        warn_errno("waitid", errno);
                    }
                    use_waitid = 0;
                }
            }
            if (!use_waitid) {
                int status = 0;
                pid_t w = waitpid(r->pid, &status, WNOHANG);
                if (w == r->pid) {
                    record_status(r, status);
                    r->group_clean = empty_group(r->pid);
                    done = 1;
                } else if (w == -1 && errno != EINTR) {
                    warn_errno("waitpid", errno);
                    done = 1;
                }
            } else if (exited) {
                reap(r, 1);
                done = 1;
            }
            if (done) {
                done_at = now_ms();
                r->duration_ms = done_at - start;
                r->exit_at = done_at;
            }
        }
        if (done && out->fd == -1 && err->fd == -1 && exec_rd == -1) {
            break;
        }
        if (done && now_ms() - done_at > PIPE_GRACE_MS) {
            /* Something outside the group (a setsid() escapee) still holds a pipe open. */
            break;
        }
        struct pollfd fds[4];
        struct sink *who[4];
        int nfds = 0;
        if (out->fd != -1) { fds[nfds].fd = out->fd; who[nfds] = out; nfds++; }
        if (err->fd != -1) { fds[nfds].fd = err->fd; who[nfds] = err; nfds++; }
        int exec_slot = -1, stdin_slot = -1;
        if (exec_rd != -1) { exec_slot = nfds; fds[nfds].fd = exec_rd; who[nfds] = NULL; nfds++; }
        if (stdin_open) { stdin_slot = nfds; fds[nfds].fd = 0; who[nfds] = NULL; nfds++; }
        for (int i = 0; i < nfds; i++) {
            fds[i].events = POLLIN;
            fds[i].revents = 0;
        }
        int timeout = 20;
        if (!done && !killed && deadline > now_ms() && deadline - now_ms() < 20) {
            timeout = (int)(deadline - now_ms());
        }
        int n = nfds ? poll(fds, (nfds_t)nfds, timeout) : (usleep(20000), 0);
        if (n == -1) {
            if (errno != EINTR) {
                warn_errno("poll", errno);
                usleep(20000);
            }
            continue;
        }
        for (int i = 0; i < nfds && n > 0; i++) {
            if (fds[i].revents == 0) {
                continue;
            }
            if (who[i] != NULL) {
                sink_read(who[i]);
            } else if (i == exec_slot) {
                ssize_t k = read(exec_rd, exec_buf + exec_got, sizeof exec_buf - exec_got);
                if (k > 0) {
                    exec_got += (size_t)k;
                }
                if (k == 0 || exec_got == sizeof exec_buf || (k < 0 && errno != EINTR && errno != EAGAIN)) {
                    if (exec_got == sizeof exec_buf) {
                        memcpy(&r->exec_errno, exec_buf, sizeof r->exec_errno);
                    }
                    close(exec_rd);
                    exec_rd = -1;
                }
            } else if (i == stdin_slot) {
                char c;
                ssize_t k = read(0, &c, 1);
                if (k == 0 || (k < 0 && errno != EINTR && errno != EAGAIN)) {
                    stdin_open = 0;
                    if (!done) {
                        r->disconnected = 1;
                        g_stop_signal = SIGHUP;
                    }
                }
            }
        }
    }
    if (exec_rd != -1) {
        close(exec_rd);
    }
    r->capture_complete = out->fd == -1 && err->fd == -1 && out->eof && err->eof;
    if (out->fd != -1) { close(out->fd); out->fd = -1; }
    if (err->fd != -1) { close(err->fd); err->fd = -1; }
}

static void wait_core(const struct opts *o, struct dumper *d, struct result *r)
{
    r->dumper_state = "timeout";
    uint64_t deadline = now_ms() + (uint64_t)(o->core_wait_ms > 0 ? o->core_wait_ms : 0);
    for (;;) {
        uint64_t t = now_ms();
        if (t >= deadline) {
            break;
        }
        uint64_t ns = (deadline - t) * 1000000ull;
        struct _pulse pulse;
        if (TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, NULL, &ns, NULL) == -1) {
            warn_errno("TimerTimeout", errno);
            r->dumper_state = "error";
            break;
        }
        if (MsgReceivePulse(d->chid, &pulse, sizeof pulse, NULL) == -1) {
            if (errno == EINTR) {
                continue;
            }
            if (errno != ETIMEDOUT) {
                warn_errno("MsgReceivePulse", errno);
                r->dumper_state = "error";
            }
            break;
        }
        if (pulse.code != DUMP_PULSE_CODE || pulse.value.sival_int != r->pid) {
            continue; /* another process's dump */
        }
        r->core_ready_ms = (long)(now_ms() - r->exit_at);
        dump_info_t info;
        memset(&info, 0, sizeof info);
        info.i.pid = r->pid;
        bound_next_call(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, DUMPER_CALL_NS);
        int rc = devctl(d->fd, DCMD_DUMPER_GETPATH, &info, sizeof info, NULL);
        info.o.dump_pathname[sizeof info.o.dump_pathname - 1] = '\0';
        if (rc != EOK || info.o.dump_pathname[0] == '\0') {
            /* The dump happened (we got its pulse) but dumper no longer knows the path,
             * for example because its list of recent dumps (dumper -E) was overrun. */
            r->dumper_state = "notified_no_path";
            r->dumper_errno = rc;
            break;
        }
        snprintf(r->dumper_core_path, sizeof r->dumper_core_path, "%s", info.o.dump_pathname);
        snprintf(r->core_path, sizeof r->core_path, "%s", info.o.dump_pathname);
        r->have_core = 1;
        r->dumper_state = "notified";
        if (o->core_dir != NULL) {
            const char *base = strrchr(r->dumper_core_path, '/');
            base = base ? base + 1 : r->dumper_core_path;
            char dst[PATH_MAX];
            if (snprintf(dst, sizeof dst, "%s/%s", o->core_dir, base) >= (int)sizeof dst) {
                r->core_move_error = "path too long";
            } else if (rename(r->dumper_core_path, dst) == -1) {
                r->core_move_error = strerror(errno);
            } else {
                snprintf(r->core_path, sizeof r->core_path, "%s", dst);
            }
        }
        break;
    }
}

static void print_result(FILE *f, const struct result *r, const struct sink *out, const struct sink *err, int with_data)
{
    int core_expected = r->signaled && dumper_signal(r->signo);
    j_head(f);
    fprintf(f, ",\"pid\":%d,\"exec_error\":", (int)r->pid);
    j_cstr(f, r->exec_errno ? strerror(r->exec_errno) : NULL);
    fprintf(f, ",\"exited\":%s,\"exit_code\":", r->exited ? "true" : "false");
    if (r->exited) {
        fprintf(f, "%d", r->exit_code);
    } else {
        fputs("null", f);
    }
    fprintf(f, ",\"signaled\":%s,\"signal\":", r->signaled ? "true" : "false");
    if (r->signaled) {
        fprintf(f, "%d", r->signo);
    } else {
        fputs("null", f);
    }
    fputs(",\"signal_name\":", f);
    j_cstr(f, r->signaled ? signal_name(r->signo) : NULL);
    fprintf(f, ",\"core_expected\":%s,\"timed_out\":%s,\"disconnected\":%s,\"runner_stopped_by\":",
            core_expected ? "true" : "false", r->timed_out ? "true" : "false", r->disconnected ? "true" : "false");
    j_cstr(f, r->stopped_by && !r->disconnected ? signal_name(r->stopped_by) : NULL);
    fprintf(f, ",\"duration_ms\":%llu,\"group_clean\":%s,\"capture_complete\":%s", (unsigned long long)r->duration_ms,
            r->group_clean ? "true" : "false", r->capture_complete ? "true" : "false");
    if (with_data) {
        fputs(",\"stdout\":", f);
        j_str(f, out->buf, out->kept);
        fputs(",\"stderr\":", f);
        j_str(f, err->buf, err->kept);
    }
    fprintf(f, ",\"stdout_bytes\":%llu,\"stdout_truncated\":%s,\"stderr_bytes\":%llu,\"stderr_truncated\":%s",
            (unsigned long long)out->total, out->total > out->kept ? "true" : "false",
            (unsigned long long)err->total, err->total > err->kept ? "true" : "false");
    fputs(",\"dumper\":", f);
    j_cstr(f, r->dumper_state);
    fputs(",\"dumper_error\":", f);
    j_cstr(f, r->dumper_errno ? strerror(r->dumper_errno) : NULL);
    fprintf(f, ",\"core_ready_ms\":%ld,\"core_path\":", r->core_ready_ms);
    j_cstr(f, r->have_core ? r->core_path : NULL);
    fputs(",\"dumper_core_path\":", f);
    j_cstr(f, r->have_core ? r->dumper_core_path : NULL);
    fputs(",\"core_move_error\":", f);
    j_cstr(f, r->core_move_error);
    j_warnings(f);
    fputs("}\n", f);
}

/* Runs the program to completion; out/err either buffer (foreground) or write files (background). */
static void run_program(const struct opts *o, struct result *r, struct sink *out, struct sink *err, struct dumper *d)
{
    memset(r, 0, sizeof *r);
    r->core_ready_ms = -1;
    dumper_register(d);
    r->dumper_state = d->state;
    if (d->fd == -1 && d->err) {
        r->dumper_errno = d->err;
    }
    int exec_rd;
    uint64_t start = now_ms();
    r->pid = start_child(o, &out->fd, &err->fd, &exec_rd);
    supervise(o, r, out, err, exec_rd, start);
    if (r->signaled && dumper_signal(r->signo)) {
        if (d->fd != -1) {
            wait_core(o, d, r);
        }
    } else if (d->fd != -1) {
        r->dumper_state = "not_applicable";
    }
    dumper_unregister(d);
}

/* ------------------------------------------------------------------ background runs */

static void write_status(const char *dir, const struct result *r, const struct sink *out, const struct sink *err)
{
    char tmp[PATH_MAX], dst[PATH_MAX];
    snprintf(tmp, sizeof tmp, "%s/.status.json.tmp", dir);
    snprintf(dst, sizeof dst, "%s/status.json", dir);
    FILE *f = fopen(tmp, "w");
    if (f == NULL) {
        return;
    }
    print_result(f, r, out, err, 0);
    if (fclose(f) == 0) {
        rename(tmp, dst); /* atomic: a poll sees either no status or the complete one */
    }
}

static int mode_background(struct opts *o, const char *dir)
{
    int rp[2];
    if (pipe(rp) == -1) {
        fail("pipe", errno);
    }
    pid_t daemon_pid = fork();
    if (daemon_pid == -1) {
        fail("fork", errno);
    }
    if (daemon_pid > 0) {
        close(rp[1]);
        int msg[3] = {0, 0, 0}; /* setup errno, child pid, lock errno */
        size_t got = 0;
        uint64_t end = now_ms() + 10000;
        while (got < sizeof msg && now_ms() < end) {
            struct pollfd p = {rp[0], POLLIN, 0};
            if (poll(&p, 1, 100) <= 0) {
                continue;
            }
            ssize_t k = read(rp[0], (char *)msg + got, sizeof msg - got);
            if (k <= 0) {
                break;
            }
            got += (size_t)k;
        }
        j_head(stdout);
        printf(",\"started\":%s,\"runner_pid\":%d,\"pid\":", got == sizeof msg && msg[0] == 0 ? "true" : "false", (int)daemon_pid);
        if (got == sizeof msg && msg[1] > 0) {
            printf("%d", msg[1]);
        } else {
            fputs("null", stdout);
        }
        fputs(",\"error\":", stdout);
        j_cstr(stdout, got < sizeof msg ? "no report from the background runner" : msg[0] ? strerror(msg[0]) : NULL);
        puts("}");
        return 0;
    }

    /* Background runner: own session, no ties to the ssh channel. */
    close(rp[0]);
    int msg[3] = {0, 0, 0};
    if (setsid() == -1) {
        msg[0] = errno;
    }
    int dn = open("/dev/null", O_RDWR);
    if (dn == -1 || dup2(dn, 0) == -1 || dup2(dn, 1) == -1 || dup2(dn, 2) == -1) {
        msg[0] = msg[0] ? msg[0] : errno;
    }
    if (dn > 2) {
        close(dn);
    }
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/runner.pid", dir);
    int lockfd = open(path, O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    struct flock fl;
    memset(&fl, 0, sizeof fl);
    fl.l_type = F_WRLCK;
    fl.l_whence = SEEK_SET;
    if (lockfd == -1 || fcntl(lockfd, F_SETLK, &fl) == -1) {
        msg[0] = msg[0] ? msg[0] : errno;
    }
    struct sink out = {-1, NULL, -1, o->max_bytes, 0, 0, 0};
    struct sink err = {-1, NULL, -1, o->max_bytes, 0, 0, 0};
    snprintf(path, sizeof path, "%s/stdout", dir);
    out.file = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    snprintf(path, sizeof path, "%s/stderr", dir);
    err.file = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (out.file == -1 || err.file == -1) {
        msg[0] = msg[0] ? msg[0] : errno;
    }
    if (msg[0] != 0) {
        (void)!write(rp[1], msg, sizeof msg);
        _exit(1);
    }

    struct result r;
    struct dumper d;
    memset(&r, 0, sizeof r);
    r.core_ready_ms = -1;
    dumper_register(&d);
    r.dumper_state = d.state;
    if (d.fd == -1 && d.err) {
        r.dumper_errno = d.err;
    }
    int exec_rd;
    uint64_t start = now_ms();
    r.pid = start_child(o, &out.fd, &err.fd, &exec_rd);
    char pidline[64];
    int pn = snprintf(pidline, sizeof pidline, "%d %d\n", (int)getpid(), (int)r.pid);
    if (write(lockfd, pidline, (size_t)pn) != pn) {
        warn_errno("write runner.pid", errno);
    }
    msg[1] = (int)r.pid;
    (void)!write(rp[1], msg, sizeof msg);
    close(rp[1]);

    supervise(o, &r, &out, &err, exec_rd, start);
    if (r.signaled && dumper_signal(r.signo)) {
        if (d.fd != -1) {
            wait_core(o, &d, &r);
        }
    } else if (d.fd != -1) {
        r.dumper_state = "not_applicable";
    }
    dumper_unregister(&d);
    close(out.file);
    close(err.file);

    write_status(dir, &r, &out, &err);
    close(lockfd); /* releases the liveness lock */
    _exit(0);
}

/* Returns the pid holding the liveness lock on dir/runner.pid, 0 if none, -1 on error. */
static pid_t lock_holder(const char *dir, pid_t *runner, pid_t *child)
{
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/runner.pid", dir);
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd == -1) {
        return -1;
    }
    char buf[64] = {0};
    ssize_t n = read(fd, buf, sizeof buf - 1);
    int rp = 0, cp = 0;
    if (n > 0 && sscanf(buf, "%d %d", &rp, &cp) == 2) {
        *runner = rp;
        *child = cp;
    }
    struct flock fl;
    memset(&fl, 0, sizeof fl);
    fl.l_type = F_WRLCK;
    fl.l_whence = SEEK_SET;
    pid_t holder = -1;
    if (fcntl(fd, F_GETLK, &fl) == 0) {
        holder = fl.l_type == F_UNLCK ? 0 : fl.l_pid;
    }
    close(fd);
    return holder;
}

static void print_chunk(const char *dir, const char *name, unsigned long long offset, size_t max)
{
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    printf(",\"%s\":", name);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    struct stat st;
    if (fd == -1 || fstat(fd, &st) == -1) {
        fputs("null", stdout);
        if (fd != -1) {
            close(fd);
        }
        return;
    }
    unsigned long long size = (unsigned long long)st.st_size;
    if (offset > size) {
        offset = size;
    }
    size_t want = size - offset < max ? (size_t)(size - offset) : max;
    char *buf = malloc(want + 1);
    ssize_t n = buf ? pread(fd, buf, want, (off_t)offset) : -1;
    if (n < 0) {
        n = 0;
    }
    printf("{\"size\":%llu,\"offset\":%llu,\"next\":%llu,\"data\":", size, offset, offset + (unsigned long long)n);
    j_str(stdout, buf ? buf : "", (size_t)n);
    fputc('}', stdout);
    free(buf);
    close(fd);
}

static int mode_poll(const char *dir, unsigned long long out_off, unsigned long long err_off, size_t max)
{
    pid_t runner = 0, child = 0;
    pid_t holder = lock_holder(dir, &runner, &child);
    j_head(stdout);
    printf(",\"alive\":%s,\"runner_pid\":%d,\"pid\":%d,\"status\":", holder > 0 ? "true" : "false", (int)runner, (int)child);
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/status.json", dir);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd == -1) {
        fputs("null", stdout);
    } else {
        char buf[8192];
        ssize_t n;
        while ((n = read(fd, buf, sizeof buf)) > 0) {
            /* our own JSON object, written atomically; strip its trailing newline */
            while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) {
                n--;
            }
            fwrite(buf, 1, (size_t)n, stdout);
        }
        close(fd);
    }
    print_chunk(dir, "stdout", out_off, max);
    print_chunk(dir, "stderr", err_off, max);
    puts("}");
    return 0;
}

static int mode_stop(const char *dir, long wait_ms)
{
    pid_t runner = 0, child = 0;
    pid_t holder = lock_holder(dir, &runner, &child);
    j_head(stdout);
    if (holder <= 0) {
        printf(",\"was_running\":false,\"forced\":false}\n");
        return 0;
    }
    if (runner <= 0) {
        runner = holder; /* started a moment ago and has not written runner.pid yet */
    }
    if (holder != runner) {
        fputs(",\"was_running\":true,\"error\":\"lock holder does not match runner.pid\"}\n", stdout);
        return 0;
    }
    int forced = 0;
    if (kill(runner, SIGTERM) == -1 && errno != ESRCH) {
        warn_errno("kill(runner)", errno);
    }
    uint64_t end = now_ms() + (uint64_t)(wait_ms > 0 ? wait_ms : 0);
    while (lock_holder(dir, &runner, &child) == holder && now_ms() < end) {
        usleep(20000);
    }
    if (lock_holder(dir, &runner, &child) == holder) {
        /* The runner did not finish in time: kill the program's group, then the runner. */
        if (child > 0) {
            kill(-child, SIGKILL);
        }
        kill(holder, SIGKILL);
        forced = 1;
    }
    printf(",\"was_running\":true,\"forced\":%s", forced ? "true" : "false");
    j_warnings(stdout);
    puts("}");
    return 0;
}

/* ------------------------------------------------------------------ main */

static long num_arg(const char *s, long lo, long hi)
{
    char *end = NULL;
    errno = 0;
    long v = strtol(s, &end, 10);
    if (errno != 0 || end == s || *end != '\0' || v < lo || v > hi) {
        fail("bad numeric option", 0);
    }
    return v;
}

int main(int argc, char *argv[])
{
    struct opts o;
    memset(&o, 0, sizeof o);
    o.timeout_ms = 30000;
    o.core_wait_ms = 10000;
    o.max_bytes = 65536;
    const char *bg_dir = NULL, *poll_dir = NULL, *stop_dir = NULL, *hash_path = NULL;
    unsigned long long out_off = 0, err_off = 0;
    size_t max_chunk = 65536;
    int opt;

    while ((opt = getopt(argc, argv, "t:m:w:C:e:R:SB:P:O:E:L:K:H:V")) != -1) {
        switch (opt) {
        case 't': o.timeout_ms = num_arg(optarg, 1, 86400000L); break;
        case 'm': o.max_bytes = (size_t)num_arg(optarg, 1, 64L << 20); break;
        case 'w': o.core_wait_ms = num_arg(optarg, 0, 600000L); break;
        case 'C': o.cwd = optarg; break;
        case 'e':
            if (o.nenv == MAX_ENV || strchr(optarg, '=') == NULL) {
                fail("bad -e NAME=VALUE", 0);
            }
            o.envs[o.nenv++] = optarg;
            break;
        case 'R': o.core_dir = optarg; break;
        case 'S': o.watch_stdin = 1; break;
        case 'B': bg_dir = optarg; break;
        case 'P': poll_dir = optarg; break;
        case 'O': out_off = (unsigned long long)num_arg(optarg, 0, LONG_MAX); break;
        case 'E': err_off = (unsigned long long)num_arg(optarg, 0, LONG_MAX); break;
        case 'L': max_chunk = (size_t)num_arg(optarg, 1, 16L << 20); break;
        case 'K': stop_dir = optarg; break;
        case 'H': hash_path = optarg; break;
        case 'V': printf("qnx-mcp-run %s\n", QMR_VERSION); return 0;
        default: fail("unknown option", 0);
        }
    }
    if (hash_path != NULL) {
        return mode_hash(hash_path);
    }
    if (poll_dir != NULL) {
        return mode_poll(poll_dir, out_off, err_off, max_chunk);
    }
    if (stop_dir != NULL) {
        return mode_stop(stop_dir, o.core_wait_ms);
    }
    if (optind >= argc) {
        fail("missing program", 0);
    }
    o.prog = &argv[optind];

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    if (sigaction(SIGHUP, &sa, NULL) == -1 || sigaction(SIGTERM, &sa, NULL) == -1 || sigaction(SIGINT, &sa, NULL) == -1) {
        fail("sigaction", errno);
    }
    signal(SIGPIPE, SIG_IGN);

    if (bg_dir != NULL) {
        o.watch_stdin = 0;
        return mode_background(&o, bg_dir);
    }

    struct sink out = {-1, malloc(o.max_bytes + 1), -1, o.max_bytes, 0, 0, 0};
    struct sink err = {-1, malloc(o.max_bytes + 1), -1, o.max_bytes, 0, 0, 0};
    if (out.buf == NULL || err.buf == NULL) {
        fail("out of memory", ENOMEM);
    }
    struct result r;
    struct dumper d;
    run_program(&o, &r, &out, &err, &d);
    print_result(stdout, &r, &out, &err, 1);
    return 0;
}
