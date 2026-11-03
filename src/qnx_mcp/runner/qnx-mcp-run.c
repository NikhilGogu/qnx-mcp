/*
 * qnx-mcp-run: target-side runner for qnx-mcp (QNX OS 8.0).
 *
 * Copyright 2026 The qnx-mcp authors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Modes, one per invocation; every mode except -X prints exactly one JSON object on stdout:
 *   qnx-mcp-run [run options] -- program [args...]     run once and report how it ended
 *   qnx-mcp-run -B dir [run options] -- program [args]  start in the background (own session);
 *       output goes to dir/stdout and dir/stderr, the final report to dir/status.json
 *   qnx-mcp-run -P dir -O out_off -E err_off -L max     poll a background run (output from cursors)
 *   qnx-mcp-run -K dir [-w wait_ms]                     stop a background run through its control FIFO
 *   qnx-mcp-run -H file [-x dir]                        size, mode, SHA-256 and real path of a regular file;
 *       -x refuses a file whose real path is inside dir
 *   qnx-mcp-run -l dir -L max                           the first max entries of a directory by name (lstat)
 *   qnx-mcp-run -Z pid -q path -s SIGNAME -w grace_ms [-f] -A root... -F dir...  stop a process that qnx-mcp did
 *       not start, if its executable path is path and lies inside a root and outside every dir; -f sends SIGKILL
 *       when it has not exited after grace_ms
 *   qnx-mcp-run -X file -L max                          copy at most max bytes of a regular file to stdout (raw)
 *   qnx-mcp-run -G dir -k keep -a age_s [-p name]...    prune run or app directories that are not in use
 *   qnx-mcp-run -D rel -r root [-x dir] [-c] [-I tmp [-n]]  check, or install tmp as, root/rel without
 *       following symlinks below root; -n never replaces an existing file
 *   qnx-mcp-run -V                                      print the version
 * Run options: -t timeout_ms, -m max bytes kept per stream, -w core_wait_ms, -C cwd,
 *   -e NAME=VALUE (repeatable), -R dir (move the core file there and lock dir/active while running),
 *   -S (stop the program when stdin reaches EOF, which is how the ssh client going away shows up here),
 *   -x dir (refuse a program that is not an executable regular file or whose real path is inside dir).
 *
 * The program runs in its own process group with stdin on /dev/null. When it exits, its exit is seen
 * with waitid(WNOWAIT): the zombie keeps the pid and the group id reserved while the rest of the group
 * is killed, the core is waited for, and only then is the zombie reaped. The deadline is a timer
 * signal, so it is enforced even if the main loop is blocked. A descendant that calls setsid() escapes
 * the group; that is not containable here.
 *
 * Crash handling uses the dumper's client interface instead of guessing file names: before the
 * program starts we register an updateable pulse with DCMD_DUMPER_NOTIFYEVENT, so dumper writes the
 * dumped pid into the pulse value. If the program dies from a signal that dumper handles, we wait
 * (bounded) for the pulse carrying its pid and ask DCMD_DUMPER_GETPATH for the exact core path.
 * Calls to dumper run in a helper thread with a bounded wait, and each is also bounded with
 * TimerTimeout (the call is skipped if that cannot be set up).
 */
#include <ctype.h>
#include <devctl.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
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
#include <sys/procfs.h>
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
#define DUMPER_CALL_MS 3000
#define MAX_PROTECT 128
#define PIPE_GRACE_MS 300
#define GROUP_GRACE_MS 2000
#define MAX_ROOTS 16
#define MAX_LIST 1000
#define LIST_ALARM_S 30

static volatile sig_atomic_t g_stop_signal;
static volatile sig_atomic_t g_child; /* the program while it is not yet reaped, else 0 */

/* Async-signal-safe: g_child is cleared before the program is reaped, so the pid is still reserved. */
static void kill_child_async(void)
{
    pid_t c = (pid_t)g_child;
    if (c > 0 && kill(-c, SIGKILL) == -1) {
        kill(c, SIGKILL); /* no group yet (setpgid not done or failed) */
    }
}

static void on_stop(int signo)
{
    g_stop_signal = signo;
    kill_child_async();
}

static void on_deadline(int signo)
{
    (void)signo;
    kill_child_async();
}

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

/* ------------------------------------------------------------------ path rules */

/* path equals dir or lies below it (both absolute, dir without a trailing '/'). */
static int under(const char *path, const char *dir)
{
    size_t l = strlen(dir);
    return strncmp(path, dir, l) == 0 && (path[l] == '\0' || path[l] == '/');
}

/* path lies inside dir as named or inside dir's real path (/tmp is a link to /data/var/tmp on these images). */
static int inside(const char *path, const char *dir)
{
    char real[PATH_MAX];
    return under(path, dir) || (realpath(dir, real) != NULL && under(path, real));
}

/* A program that -x allows: an executable regular file whose real path is not inside forbidden. */
static void check_program(const char *prog, const char *forbidden)
{
    struct stat st;
    char real[PATH_MAX];
    if (stat(prog, &st) == -1 || realpath(prog, real) == NULL) {
        fail("program not found", errno);
    }
    if (inside(real, forbidden)) {
        fail("program is inside qnx-mcp's own directory", 0);
    }
    if (!S_ISREG(st.st_mode) || (st.st_mode & 0111) == 0 || access(prog, X_OK) == -1) {
        fail("program is not an executable regular file", 0);
    }
}

static int mode_hash(const char *path, const char *forbidden)
{
    /* O_NONBLOCK: opening a FIFO must not wait for a writer; it is refused below like any non-regular file */
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    struct stat st;
    char real[PATH_MAX];
    if (fd == -1 || fstat(fd, &st) == -1) {
        fail("open", errno);
    }
    if (!S_ISREG(st.st_mode)) {
        fail("not a regular file", 0);
    }
    if (realpath(path, real) == NULL) {
        fail("realpath", errno);
    }
    if (forbidden != NULL && inside(real, forbidden)) {
        fail("inside qnx-mcp's own directory", 0);
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
    fputs(",\"real_path\":", stdout);
    j_cstr(stdout, real);
    printf(",\"size\":%llu,\"mode\":\"%04o\",\"sha256\":\"%s\",\"executable\":%s}\n", (unsigned long long)size,
           (unsigned)(st.st_mode & 07777), hex, (st.st_mode & 0111) != 0 && access(path, X_OK) == 0 ? "true" : "false");
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

static int signal_number(const char *name)
{
    for (size_t i = 0; i < sizeof k_signals / sizeof k_signals[0]; i++) {
        if (strcmp(k_signals[i].name, name) == 0) {
            return k_signals[i].no;
        }
    }
    return 0;
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
    int wedged; /* a call did not return in time: make no further calls */
    const char *state;
    int err;
};

/* Bound the next blocking kernel call. Returns 0, or -1 if the bound could not be set up, in which
 * case the caller must not make the call. */
static int bound_next_call(int flags, uint64_t ns)
{
    uint64_t t = ns;
    if (TimerTimeout(CLOCK_MONOTONIC, flags, NULL, &t, NULL) == -1) {
        warn_errno("TimerTimeout", errno);
        return -1;
    }
    return 0;
}

/* One call to dumper, run in a helper thread so that a wedged dumper (whose channel may ignore
 * unblock requests) cannot stall the runner: the main thread waits at most DUMPER_CALL_MS. */
struct dcall {
    int op; /* 0 register, 1 getpath, 2 remove */
    int fd;
    int coid;
    pid_t pid;
    int rc;
    char path[PATH_MAX];
    int done[2];
};

static void *dcall_main(void *arg)
{
    struct dcall *c = arg;
    if (c->op == 0) {
        int fd = open("/proc/dumper", O_RDONLY | O_CLOEXEC);
        if (fd == -1) {
            c->rc = errno;
        } else {
            struct sigevent ev;
            SIGEV_PULSE_INIT(&ev, c->coid, sched_get_priority_max(SCHED_RR), DUMP_PULSE_CODE, -1);
            SIGEV_MAKE_UPDATEABLE(&ev);
            if (MsgRegisterEvent(&ev, fd) == -1) {
                c->rc = errno;
            } else if (bound_next_call(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, DUMPER_CALL_NS) != 0) {
                c->rc = EINVAL;
            } else {
                c->rc = devctl(fd, DCMD_DUMPER_NOTIFYEVENT, &ev, sizeof ev, NULL);
            }
            if (c->rc == EOK) {
                c->fd = fd;
            } else {
                close(fd);
            }
        }
    } else if (bound_next_call(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, DUMPER_CALL_NS) != 0) {
        c->rc = EINVAL;
    } else if (c->op == 1) {
        dump_info_t info;
        memset(&info, 0, sizeof info);
        info.i.pid = c->pid;
        c->rc = devctl(c->fd, DCMD_DUMPER_GETPATH, &info, sizeof info, NULL);
        info.o.dump_pathname[sizeof info.o.dump_pathname - 1] = '\0';
        snprintf(c->path, sizeof c->path, "%s", c->rc == EOK ? info.o.dump_pathname : "");
    } else {
        c->rc = devctl(c->fd, DCMD_DUMPER_REMOVEEVENT, NULL, 0, NULL);
    }
    (void)!write(c->done[1], "x", 1);
    return NULL;
}

/* Returns the finished call (caller frees it), or NULL if it failed to start or did not finish in time. */
static struct dcall *dcall_run(struct dumper *d, int op, pid_t pid)
{
    if (d->wedged) {
        return NULL;
    }
    struct dcall *c = calloc(1, sizeof *c);
    if (c == NULL || pipe(c->done) == -1) {
        free(c);
        return NULL;
    }
    c->op = op;
    c->fd = d->fd;
    c->coid = d->coid;
    c->pid = pid;
    pthread_t th;
    if (pthread_create(&th, NULL, dcall_main, c) != 0) {
        close(c->done[0]);
        close(c->done[1]);
        free(c);
        return NULL;
    }
    pthread_detach(th);
    uint64_t end = now_ms() + DUMPER_CALL_MS;
    for (;;) {
        uint64_t now = now_ms();
        struct pollfd p = {c->done[0], POLLIN, 0};
        int n = now < end ? poll(&p, 1, (int)(end - now)) : 0;
        if (n > 0) {
            break;
        }
        if (n == 0 || errno != EINTR) {
            d->wedged = 1; /* the helper still owns c; it is leaked on purpose */
            warn_errno("dumper call did not return", ETIMEDOUT);
            return NULL;
        }
    }
    close(c->done[0]);
    close(c->done[1]);
    return c;
}

static void dumper_register(struct dumper *d)
{
    memset(d, 0, sizeof *d);
    d->chid = d->coid = d->fd = -1;
    d->state = "unavailable";
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
    struct dcall *c = dcall_run(d, 0, 0);
    if (c == NULL) {
        d->err = ETIMEDOUT;
        d->state = "error";
        return;
    }
    d->err = c->rc;
    d->fd = c->rc == EOK ? c->fd : -1;
    free(c);
    if (d->fd != -1) {
        d->state = "registered";
    }
}

static void dumper_unregister(struct dumper *d)
{
    if (d->fd != -1) {
        struct dcall *c = dcall_run(d, 2, 0);
        if (c != NULL && c->rc != EOK) {
            warn_errno("DCMD_DUMPER_REMOVEEVENT", c->rc);
        }
        free(c);
        if (!d->wedged) {
            close(d->fd);
        }
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
    int zombie_held;        /* exit seen with WNOWAIT: pid and group id stay reserved until reap() */
    uint64_t duration_ms;
    uint64_t exit_at;
    long core_ready_ms;
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

/* Processes other than the leader whose process group is pgid (scan of /proc). */
static int group_members(pid_t pgid)
{
    DIR *d = opendir("/proc");
    if (d == NULL) {
        return -1;
    }
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (!isdigit((unsigned char)e->d_name[0])) {
            continue;
        }
        pid_t p = (pid_t)strtol(e->d_name, NULL, 10);
        if (p != pgid && getpgid(p) == pgid) {
            n++;
        }
    }
    closedir(d);
    return n;
}

/* Called while the exited leader is still an unreaped zombie, so the group id cannot be reused. */
static int clean_group(pid_t pgid)
{
    uint64_t end = now_ms() + GROUP_GRACE_MS;
    for (;;) {
        kill(-pgid, SIGKILL);
        int n = group_members(pgid);
        if (n == 0) {
            return 1;
        }
        if (n < 0 || now_ms() >= end) {
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
        signal(SIGALRM, SIG_DFL);
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
    g_child = pid; /* from here the deadline and stop handlers may signal the (unreaped) child */
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

/* Release the zombie. After this the pid may be reused, so nothing signals it again. */
static void reap(struct result *r)
{
    if (!r->zombie_held) {
        return;
    }
    g_child = 0;
    int status = 0;
    for (;;) {
        pid_t w = waitpid(r->pid, &status, 0);
        if (w == r->pid || (w == -1 && errno != EINTR)) {
            if (w == -1) {
                warn_errno("waitpid", errno);
            }
            break;
        }
    }
    r->zombie_held = 0;
}

static void supervise(const struct opts *o, struct result *r, struct sink *out, struct sink *err, int exec_rd, int ctl_rd, uint64_t start)
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
                kill_child_async();
                r->timed_out = killed = 1;
            } else if (!killed && g_stop_signal) {
                kill_child_async();
                r->stopped_by = g_stop_signal;
                killed = 1;
            }
            if (use_waitid) {
                siginfo_t si;
                memset(&si, 0, sizeof si);
                if (waitid(P_PID, (id_t)r->pid, &si, WEXITED | WNOHANG | WNOWAIT) == 0) {
                    if (si.si_pid == r->pid) {
                        /* Exit seen, zombie kept: pid and group id stay reserved while we clean up. */
                        r->zombie_held = 1;
                        r->exited = si.si_code == CLD_EXITED && r->exec_errno == 0;
                        r->exit_code = si.si_code == CLD_EXITED ? si.si_status : 0;
                        r->signaled = si.si_code == CLD_KILLED || si.si_code == CLD_DUMPED;
                        r->signo = r->signaled ? si.si_status : 0;
                        r->group_clean = clean_group(r->pid);
                        done = 1;
                    }
                } else if (errno != EINTR) {
                    if (errno != EINVAL && errno != ENOSYS) {
                        warn_errno("waitid", errno);
                    }
                    use_waitid = 0;
                }
            }
            if (!use_waitid && !done) {
                /* Fallback: reaping releases the pid, so the group is not signalled afterwards. */
                int status = 0;
                g_child = 0;
                pid_t w = waitpid(r->pid, &status, WNOHANG);
                if (w == r->pid) {
                    record_status(r, status);
                    r->group_clean = 0;
                    done = 1;
                } else if (w == -1 && errno != EINTR) {
                    warn_errno("waitpid", errno);
                    done = 1;
                } else {
                    g_child = r->pid;
                }
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
        struct pollfd fds[5];
        struct sink *who[5];
        int nfds = 0;
        if (out->fd != -1) { fds[nfds].fd = out->fd; who[nfds] = out; nfds++; }
        if (err->fd != -1) { fds[nfds].fd = err->fd; who[nfds] = err; nfds++; }
        int exec_slot = -1, stdin_slot = -1, ctl_slot = -1;
        if (exec_rd != -1) { exec_slot = nfds; fds[nfds].fd = exec_rd; who[nfds] = NULL; nfds++; }
        if (stdin_open) { stdin_slot = nfds; fds[nfds].fd = 0; who[nfds] = NULL; nfds++; }
        if (ctl_rd != -1 && !done) { ctl_slot = nfds; fds[nfds].fd = ctl_rd; who[nfds] = NULL; nfds++; }
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
                        if (r->exited) {
                            r->exited = 0; /* the 127 came from the failed exec, not the program */
                        }
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
                        kill_child_async();
                    }
                }
            } else if (i == ctl_slot) {
                char buf[64];
                ssize_t k = read(ctl_rd, buf, sizeof buf - 1);
                if (k > 0) {
                    buf[k] = '\0';
                    if (strstr(buf, "stop") != NULL) {
                        g_stop_signal = SIGTERM;
                        kill_child_async();
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

/* Runs while the program is still an unreaped zombie (when waitid is available), so a pulse that
 * names its pid cannot come from another process that reused the pid. */
static void wait_core(const struct opts *o, struct dumper *d, struct result *r)
{
    r->dumper_state = "timeout";
    uint64_t deadline = now_ms() + (uint64_t)(o->core_wait_ms > 0 ? o->core_wait_ms : 0);
    for (;;) {
        uint64_t t = now_ms();
        if (t >= deadline) {
            break;
        }
        struct _pulse pulse;
        if (bound_next_call(_NTO_TIMEOUT_RECEIVE, (deadline - t) * 1000000ull) != 0) {
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
        struct dcall *c = dcall_run(d, 1, r->pid);
        if (c == NULL || c->rc != EOK || c->path[0] == '\0') {
            /* The dump happened (we got its pulse) but dumper did not give a path, for example
             * because its list of recent dumps (dumper -E) was overrun, or it did not answer. */
            r->dumper_state = "notified_no_path";
            r->dumper_errno = c == NULL ? ETIMEDOUT : c->rc;
            free(c);
            break;
        }
        snprintf(r->dumper_core_path, sizeof r->dumper_core_path, "%s", c->path);
        snprintf(r->core_path, sizeof r->core_path, "%s", c->path);
        free(c);
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

static void arm_deadline(long ms)
{
    timer_t tid;
    struct sigevent ev;
    SIGEV_SIGNAL_INIT(&ev, SIGALRM);
    struct itimerspec its;
    memset(&its, 0, sizeof its);
    its.it_value.tv_sec = ms / 1000;
    its.it_value.tv_nsec = (ms % 1000) * 1000000L;
    if (timer_create(CLOCK_MONOTONIC, &ev, &tid) == -1 || timer_settime(tid, 0, &its, NULL) == -1) {
        warn_errno("deadline timer", errno); /* the main loop's own deadline check still applies */
    }
}

/* Runs the program to completion; out/err either buffer (foreground) or write files (background). */
static void run_program(const struct opts *o, struct result *r, struct sink *out, struct sink *err, int ctl_rd)
{
    struct dumper d;
    memset(r, 0, sizeof *r);
    r->core_ready_ms = -1;
    dumper_register(&d);
    r->dumper_state = d.state;
    if (d.fd == -1 && d.err) {
        r->dumper_errno = d.err;
    }
    int exec_rd;
    uint64_t start = now_ms();
    r->pid = start_child(o, &out->fd, &err->fd, &exec_rd);
    arm_deadline(o->timeout_ms);
    supervise(o, r, out, err, exec_rd, ctl_rd, start);
    if (r->signaled && dumper_signal(r->signo)) {
        if (d.fd != -1) {
            wait_core(o, &d, r);
        }
    } else if (d.fd != -1) {
        r->dumper_state = "not_applicable";
    }
    reap(r);
    dumper_unregister(&d);
}

static int hold_lock(const char *path)
{
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
    struct flock fl;
    memset(&fl, 0, sizeof fl);
    fl.l_type = F_WRLCK;
    fl.l_whence = SEEK_SET;
    if (fd == -1 || fcntl(fd, F_SETLK, &fl) == -1) {
        if (fd != -1) {
            close(fd);
        }
        return -1;
    }
    return fd;
}

/* Returns the pid holding the write lock on path, 0 if none, -1 if path cannot be opened. */
static pid_t lock_holder_of(const char *path)
{
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd == -1) {
        return -1;
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
        int msg[2] = {0, 0}; /* setup errno, program pid */
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
        int ok = got == sizeof msg && msg[0] == 0 && msg[1] > 0;
        if (!ok) {
            /* No usable report: the daemon is still our unreaped child, so killing it by pid is safe.
             * If it got as far as starting the program, its deadline timer dies with it, so the
             * program's group is killed here too while the daemon (its parent) is not yet reaped. */
            if (got == sizeof msg && msg[1] > 0) {
                kill(-msg[1], SIGKILL);
            }
            kill(daemon_pid, SIGKILL);
            waitpid(daemon_pid, NULL, 0);
        }
        j_head(stdout);
        printf(",\"started\":%s,\"runner_pid\":%d,\"pid\":", ok ? "true" : "false", (int)daemon_pid);
        if (ok) {
            printf("%d", msg[1]);
        } else {
            fputs("null", stdout);
        }
        fputs(",\"error\":", stdout);
        j_cstr(stdout, ok ? NULL : got < sizeof msg ? "the background runner did not report; it was stopped" : strerror(msg[0] ? msg[0] : EIO));
        puts("}");
        return 0;
    }

    /* Background runner: own session, no ties to the ssh channel. */
    close(rp[0]);
    int msg[2] = {0, 0};
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
    int lockfd = hold_lock(path);
    if (lockfd == -1) {
        msg[0] = msg[0] ? msg[0] : errno;
    }
    /* Control FIFO: run_stop writes "stop" here; only this runner reads it. A second (write) end
     * stays open so poll() does not report hang-up while no stopper is connected. */
    snprintf(path, sizeof path, "%s/ctl", dir);
    int ctl_rd = -1, ctl_wr = -1;
    if (mkfifo(path, 0600) == -1 && errno != EEXIST) {
        msg[0] = msg[0] ? msg[0] : errno;
    } else {
        ctl_rd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        ctl_wr = open(path, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
        if (ctl_rd == -1 || ctl_wr == -1) {
            msg[0] = msg[0] ? msg[0] : errno;
        }
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
    arm_deadline(o->timeout_ms);
    char pidline[64];
    int pn = snprintf(pidline, sizeof pidline, "%d %d\n", (int)getpid(), (int)r.pid);
    if (write(lockfd, pidline, (size_t)pn) != pn) {
        warn_errno("write runner.pid", errno);
    }
    msg[1] = (int)r.pid;
    (void)!write(rp[1], msg, sizeof msg);
    close(rp[1]);

    supervise(o, &r, &out, &err, exec_rd, ctl_rd, start);
    if (r.signaled && dumper_signal(r.signo)) {
        if (d.fd != -1) {
            wait_core(o, &d, &r);
        }
    } else if (d.fd != -1) {
        r.dumper_state = "not_applicable";
    }
    reap(&r);
    dumper_unregister(&d);
    close(out.file);
    close(err.file);
    write_status(dir, &r, &out, &err);
    close(lockfd); /* releases the liveness lock */
    _exit(0);
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
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/runner.pid", dir);
    pid_t holder = lock_holder_of(path);
    j_head(stdout);
    printf(",\"alive\":%s,\"status\":", holder > 0 ? "true" : "false");
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

/* Stop a background run through its control FIFO: only the runner that owns the run reads it, and
 * no signal is sent to a pid that might have been reused. */
static int mode_stop(const char *dir, long wait_ms)
{
    char path[PATH_MAX], lock[PATH_MAX];
    snprintf(path, sizeof path, "%s/ctl", dir);
    snprintf(lock, sizeof lock, "%s/runner.pid", dir);
    j_head(stdout);
    int fd = open(path, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd == -1) {
        /* ENXIO: nobody has the FIFO open, so the runner is gone */
        printf(",\"was_running\":false,\"acknowledged\":false}\n");
        return 0;
    }
    if (write(fd, "stop\n", 5) != 5) {
        warn_errno("write ctl", errno);
    }
    close(fd);
    uint64_t end = now_ms() + (uint64_t)(wait_ms > 0 ? wait_ms : 0);
    while (lock_holder_of(lock) > 0 && now_ms() < end) {
        usleep(20000);
    }
    printf(",\"was_running\":true,\"acknowledged\":%s", lock_holder_of(lock) > 0 ? "false" : "true");
    j_warnings(stdout);
    puts("}");
    return 0;
}

/* ------------------------------------------------------------------ maintenance and files */

static int rm_entry(const char *p, const struct stat *st, int flag, struct FTW *ftw)
{
    (void)st;
    (void)flag;
    (void)ftw;
    return remove(p) == 0 || errno == ENOENT ? 0 : -1;
}

struct dent {
    char name[NAME_MAX + 1];
    time_t mtime;
};

static int newest_first(const void *a, const void *b)
{
    const struct dent *x = a, *y = b;
    return x->mtime < y->mtime ? 1 : x->mtime > y->mtime ? -1 : strcmp(x->name, y->name);
}

/* Remove subdirectories of dir beyond the newest `keep`, except those younger than age_s, those named
 * in protect[], and those whose runner.pid or active file is locked by a live runner. */
static int mode_prune(const char *dir, long keep, long age_s, char **protect, int nprotect)
{
    DIR *d = opendir(dir);
    j_head(stdout);
    if (d == NULL) {
        printf(",\"removed\":0,\"skipped_live\":0}\n");
        return 0;
    }
    size_t cap = 64, n = 0;
    struct dent *v = malloc(cap * sizeof *v);
    struct dirent *e;
    while (v != NULL && (e = readdir(d)) != NULL) {
        if (e->d_name[0] == '.') {
            continue;
        }
        char p[PATH_MAX];
        struct stat st;
        snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
        if (lstat(p, &st) == -1 || !S_ISDIR(st.st_mode)) {
            continue;
        }
        if (n == cap) {
            struct dent *nv = realloc(v, (cap *= 2) * sizeof *v);
            if (nv == NULL) {
                break;
            }
            v = nv;
        }
        snprintf(v[n].name, sizeof v[n].name, "%s", e->d_name);
        v[n].mtime = st.st_mtime;
        n++;
    }
    closedir(d);
    if (v != NULL) {
        qsort(v, n, sizeof *v, newest_first);
    }
    int removed = 0, live = 0;
    time_t now = time(NULL);
    for (size_t i = (size_t)keep; v != NULL && i < n; i++) {
        int skip = now - v[i].mtime < age_s;
        for (int j = 0; !skip && j < nprotect; j++) {
            skip = strcmp(protect[j], v[i].name) == 0;
        }
        char p[PATH_MAX];
        snprintf(p, sizeof p, "%s/%s/runner.pid", dir, v[i].name);
        if (!skip && lock_holder_of(p) > 0) {
            skip = 1;
            live++;
        }
        snprintf(p, sizeof p, "%s/%s/active", dir, v[i].name);
        if (!skip && lock_holder_of(p) > 0) {
            skip = 1;
            live++;
        }
        if (skip) {
            continue;
        }
        snprintf(p, sizeof p, "%s/%s", dir, v[i].name);
        if (nftw(p, rm_entry, 16, FTW_DEPTH | FTW_PHYS) == 0) {
            removed++;
        }
    }
    free(v);
    printf(",\"removed\":%d,\"skipped_live\":%d}\n", removed, live);
    return 0;
}

/* Copy at most max bytes of a regular file to stdout, raw (the one mode whose output is not JSON). */
static int mode_cat(const char *path, size_t max)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    struct stat st;
    if (fd == -1 || fstat(fd, &st) == -1 || !S_ISREG(st.st_mode)) {
        return 3;
    }
    char buf[65536];
    size_t left = max;
    while (left > 0) {
        ssize_t k = read(fd, buf, left < sizeof buf ? left : sizeof buf);
        if (k == 0) {
            break;
        }
        if (k < 0) {
            if (errno == EINTR) {
                continue;
            }
            return 4;
        }
        for (ssize_t off = 0; off < k;) {
            ssize_t w = write(1, buf + off, (size_t)(k - off));
            if (w < 0) {
                if (errno == EINTR) {
                    continue;
                }
                return 5;
            }
            off += w;
        }
        left -= (size_t)k;
    }
    return 0;
}

/* Walk from root to the parent of root/rel without following symlinks below root (root itself may be
 * a system link such as /tmp -> /data/var/tmp). Returns a descriptor of the parent directory. */
static int open_parent(const char *root, const char *rel, int create, char *base, size_t baselen, char *real, size_t reallen)
{
    if (realpath(root, real) == NULL) {
        fail("bad root", errno);
    }
    int fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd == -1) {
        fail("open root", errno);
    }
    char buf[PATH_MAX];
    snprintf(buf, sizeof buf, "%s", rel);
    char *save = NULL, *comp = strtok_r(buf, "/", &save);
    while (comp != NULL) {
        char *next = strtok_r(NULL, "/", &save);
        if (next == NULL) {
            snprintf(base, baselen, "%s", comp);
            return fd;
        }
        if (strcmp(comp, ".") == 0 || strcmp(comp, "..") == 0) {
            fail("bad path component", 0);
        }
        int nfd = openat(fd, comp, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (nfd == -1 && errno == ENOENT && create) {
            if (mkdirat(fd, comp, 0755) == -1 && errno != EEXIST) {
                fail("mkdir", errno);
            }
            nfd = openat(fd, comp, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        }
        if (nfd == -1) {
            fail("a parent directory is a symlink or not a directory", errno);
        }
        close(fd);
        fd = nfd;
        size_t l = strlen(real);
        snprintf(real + l, reallen - l, "/%s", comp);
        comp = next;
    }
    fail("empty destination", 0);
    return -1;
}

static void check_forbidden(const char *real_parent, const char *base, const char *forbidden)
{
    char forb[PATH_MAX], full[PATH_MAX];
    if (forbidden == NULL || realpath(forbidden, forb) == NULL) {
        return;
    }
    size_t fl = strlen(forb);
    snprintf(full, sizeof full, "%s/%s", real_parent, base);
    if (strcmp(full, forb) == 0 || (strncmp(full, forb, fl) == 0 && full[fl] == '/')) {
        fail("destination is inside qnx-mcp's own directory", 0);
    }
}

/* -D dest: check (and with -c create the parents of) a file_put destination; -I tmp: install tmp
 * (in the same directory) as dest, with -n refusing to replace an existing file. */
static int mode_dest(const char *root, const char *rel, const char *forbidden, int create, const char *install, int no_replace)
{
    char base[NAME_MAX + 1], real[PATH_MAX];
    int dfd = open_parent(root, rel, create, base, sizeof base, real, sizeof real);
    check_forbidden(real, base, forbidden);
    struct stat st;
    int exists = fstatat(dfd, base, &st, AT_SYMLINK_NOFOLLOW) == 0;
    if (exists && S_ISDIR(st.st_mode)) {
        fail("destination is a directory", 0);
    }
    if (exists && S_ISLNK(st.st_mode)) {
        fail("destination is a symlink", 0);
    }
    if (exists && !S_ISREG(st.st_mode)) {
        fail("destination is not a regular file", 0);
    }
    if (install != NULL) {
        struct stat ts;
        if (strchr(install, '/') != NULL || fstatat(dfd, install, &ts, AT_SYMLINK_NOFOLLOW) == -1 || !S_ISREG(ts.st_mode)) {
            fail("temporary file missing", errno);
        }
        if (no_replace) {
            if (linkat(dfd, install, dfd, base, 0) == -1) {
                fail(errno == EEXIST ? "destination exists" : "link", errno);
            }
            unlinkat(dfd, install, 0);
        } else if (renameat(dfd, install, dfd, base) == -1) {
            fail("rename", errno);
        }
    }
    close(dfd);
    j_head(stdout);
    printf(",\"exists\":%s,\"installed\":%s,\"real_parent\":", exists ? "true" : "false", install ? "true" : "false");
    j_cstr(stdout, real);
    puts("}");
    return 0;
}

/* ------------------------------------------------------------------ directory listing */

static const char *type_name(mode_t m)
{
    return S_ISREG(m) ? "file" : S_ISDIR(m) ? "dir" : S_ISLNK(m) ? "symlink" : S_ISCHR(m) ? "char" : S_ISBLK(m) ? "block"
         : S_ISFIFO(m) ? "fifo" : S_ISSOCK(m) ? "socket" : "other";
}

/* -l dir -L max: count every entry and report the first max by name (byte order), each with lstat, so
 * links are described and never followed. Memory stays bounded by max. */
static int mode_list(const char *dir, size_t max)
{
    alarm(LIST_ALARM_S); /* a stat that blocks in a wedged resource manager must not keep the runner forever */
    if (max > MAX_LIST) {
        max = MAX_LIST;
    }
    DIR *d = opendir(dir);
    if (d == NULL) {
        fail(errno == ENOENT ? "no such directory" : errno == ENOTDIR ? "not a directory" : "opendir", errno);
    }
    char real[PATH_MAX];
    if (realpath(dir, real) == NULL) {
        fail("realpath", errno);
    }
    char **keep = calloc(max ? max : 1, sizeof *keep);
    if (keep == NULL) {
        fail("out of memory", ENOMEM);
    }
    size_t n = 0;
    unsigned long long total = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) {
            continue;
        }
        total++;
        if (max == 0 || (n == max && strcmp(e->d_name, keep[max - 1]) >= 0)) {
            continue;
        }
        size_t lo = 0, hi = n;
        while (lo < hi) {
            size_t mid = (lo + hi) / 2;
            if (strcmp(keep[mid], e->d_name) < 0) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        char *name = strdup(e->d_name);
        if (name == NULL) {
            fail("out of memory", ENOMEM);
        }
        if (n == max) {
            free(keep[--n]);
        }
        memmove(keep + lo + 1, keep + lo, (n - lo) * sizeof *keep);
        keep[lo] = name;
        n++;
    }
    int dfd = dirfd(d);
    j_head(stdout);
    fputs(",\"path\":", stdout);
    j_cstr(stdout, dir);
    fputs(",\"real_path\":", stdout);
    j_cstr(stdout, real);
    printf(",\"total\":%llu,\"entries\":[", total);
    for (size_t i = 0; i < n; i++) {
        struct stat st;
        fputs(i ? ",{\"name\":" : "{\"name\":", stdout);
        j_cstr(stdout, keep[i]);
        if (fstatat(dfd, keep[i], &st, AT_SYMLINK_NOFOLLOW) == -1) {
            fputs(",\"error\":", stdout);
            j_cstr(stdout, strerror(errno)); /* removed since readdir */
            fputc('}', stdout);
            continue;
        }
        printf(",\"type\":\"%s\",\"size\":%lld,\"mode\":\"%04o\",\"mtime\":%lld", type_name(st.st_mode), (long long)st.st_size,
               (unsigned)(st.st_mode & 07777), (long long)st.st_mtime);
        if (S_ISLNK(st.st_mode)) {
            char target[PATH_MAX];
            ssize_t k = readlinkat(dfd, keep[i], target, sizeof target);
            fputs(",\"link_target\":", stdout);
            if (k >= 0) {
                j_str(stdout, target, (size_t)k);
            } else {
                fputs("null", stdout);
            }
        }
        fputc('}', stdout);
    }
    puts("]}");
    closedir(d);
    return 0;
}

/* ------------------------------------------------------------------ stopping a process qnx-mcp did not start */

/* The process is opened through /proc/<pid>/ctl and inspected and signalled only through that descriptor.
 * While it is open the process entry stays (a dying process shows _NTO_PF_TERMING until it is closed), so
 * the checks and the signal apply to one process and cannot reach a later process with the same pid. */
static int proc_exiting(int fd)
{
    procfs_info info;
    memset(&info, 0, sizeof info);
    return devctl(fd, DCMD_PROC_INFO, &info, sizeof info, NULL) != EOK || (info.flags & (_NTO_PF_TERMING | _NTO_PF_ZOMBIE)) != 0;
}

static int proc_signal(int fd, int signo)
{
    procfs_signal s;
    memset(&s, 0, sizeof s);
    s.signo = signo;
    s.code = SI_USER;
    return devctl(fd, DCMD_PROC_SIGNAL, &s, sizeof s, NULL);
}

static int proc_wait_exit(int fd, uint64_t until)
{
    while (!proc_exiting(fd)) {
        if (now_ms() >= until) {
            return 0;
        }
        usleep(20000);
    }
    return 1;
}

static void refuse_exe(const char *what, const char *exe, const char *real)
{
    j_head(stdout);
    fputs(",\"usage_error\":", stdout);
    j_cstr(stdout, what);
    fputs(",\"executable\":", stdout);
    j_cstr(stdout, exe);
    fputs(",\"real_path\":", stdout);
    j_cstr(stdout, real);
    puts("}");
    fflush(stdout);
    exit(2);
}

static int mode_pstop(long pid, const char *expect, const char *signame, long grace_ms, int force, char **roots, int nroots, char **forbid, int nforbid)
{
    int signo = signame ? signal_number(signame) : 0;
    if (signo <= 0 || expect == NULL || nroots == 0) {
        fail("-Z needs -q path, -s SIGNAME and -A root", 0);
    }
    if (pid <= 1) {
        fail("refused: pid 1 is procnto", 0);
    }
    char p[64];
    snprintf(p, sizeof p, "/proc/%ld/ctl", pid);
    int fd = open(p, O_RDWR | O_CLOEXEC);
    if (fd == -1) {
        fail(errno == ENOENT ? "no such process" : "open process", errno);
    }
    procfs_info info;
    memset(&info, 0, sizeof info);
    int e = devctl(fd, DCMD_PROC_INFO, &info, sizeof info, NULL);
    if (e != EOK || info.pid != (pid_t)pid) {
        fail("process information", e);
    }
    struct {
        procfs_debuginfo d;
        char room[PATH_MAX + 1];
    } dbg;
    memset(&dbg, 0, sizeof dbg);
    e = devctl(fd, DCMD_PROC_MAPDEBUG_BASE, &dbg, sizeof dbg - 1, NULL); /* the last byte stays NUL */
    if (e != EOK) {
        fail("executable path", e);
    }
    if (strcmp(dbg.d.path, expect) != 0) {
        fail("refused: the process's executable path is not the one pidin reported", 0);
    }
    if (info.crit_proc) {
        fail("refused: critical process", 0);
    }
    char exe[PATH_MAX + 2], real[PATH_MAX];
    snprintf(exe, sizeof exe, "%s%s", dbg.d.path[0] == '/' ? "" : "/", dbg.d.path); /* pidin shows most paths without the leading '/' */
    if (realpath(exe, real) == NULL) {
        fail("refused: the executable path does not resolve", errno);
    }
    for (int i = 0; i < nforbid; i++) {
        if (inside(exe, forbid[i]) || inside(real, forbid[i])) {
            refuse_exe("refused: the executable is in a system or qnx-mcp directory", exe, real);
        }
    }
    int in_exe = 0, in_real = 0;
    for (int i = 0; i < nroots; i++) {
        in_exe |= inside(exe, roots[i]);
        in_real |= inside(real, roots[i]);
    }
    if (!in_exe || !in_real) {
        refuse_exe("refused: the executable is outside the allowed roots", exe, real);
    }
    int already = proc_exiting(fd), exited = already, killed = 0;
    uint64_t start = now_ms();
    if (!already) {
        e = proc_signal(fd, signo);
        if (e != EOK) {
            fail("signal", e);
        }
        exited = proc_wait_exit(fd, start + (uint64_t)grace_ms);
        if (!exited && force && signo != SIGKILL) {
            e = proc_signal(fd, SIGKILL);
            if (e != EOK) {
                warn_errno("SIGKILL", e);
            } else {
                killed = 1;
                exited = proc_wait_exit(fd, now_ms() + GROUP_GRACE_MS);
            }
        }
    }
    long waited = (long)(now_ms() - start);
    close(fd);
    j_head(stdout);
    printf(",\"pid\":%ld,\"ppid\":%ld,\"uid\":%ld,\"path\":", pid, (long)info.parent, (long)info.uid);
    j_cstr(stdout, dbg.d.path);
    fputs(",\"executable\":", stdout);
    j_cstr(stdout, exe);
    fputs(",\"real_path\":", stdout);
    j_cstr(stdout, real);
    printf(",\"signal\":\"%s\",\"already_exiting\":%s,\"signal_sent\":%s,\"sigkill_sent\":%s,\"exited\":%s,\"waited_ms\":%ld",
           signame, already ? "true" : "false", already ? "false" : "true", killed ? "true" : "false", exited ? "true" : "false", waited);
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
    const char *bg_dir = NULL, *poll_dir = NULL, *stop_dir = NULL, *hash_path = NULL, *prune_dir = NULL, *cat_path = NULL;
    const char *dest = NULL, *root = NULL, *forbidden = NULL, *install = NULL, *list_dir = NULL, *expect = NULL, *signame = NULL;
    char *protect[MAX_PROTECT], *roots[MAX_ROOTS], *forbid[MAX_ROOTS];
    int nprotect = 0, create = 0, no_replace = 0, nroots = 0, nforbid = 0, force = 0;
    long stop_pid = 0;
    long keep = 20, age_s = 300;
    unsigned long long out_off = 0, err_off = 0;
    size_t max_chunk = 65536;
    int opt;

    while ((opt = getopt(argc, argv, "t:m:w:C:e:R:SB:P:O:E:L:K:H:G:k:a:p:X:D:r:x:cI:nVl:Z:q:s:fA:F:")) != -1) {
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
        case 'L': max_chunk = (size_t)num_arg(optarg, 1, LONG_MAX); break;
        case 'K': stop_dir = optarg; break;
        case 'H': hash_path = optarg; break;
        case 'G': prune_dir = optarg; break;
        case 'k': keep = num_arg(optarg, 0, 100000); break;
        case 'a': age_s = num_arg(optarg, 0, 86400L * 365); break;
        case 'p':
            if (nprotect == MAX_PROTECT) {
                fail("too many -p", 0);
            }
            protect[nprotect++] = optarg;
            break;
        case 'X': cat_path = optarg; break;
        case 'D': dest = optarg; break;
        case 'r': root = optarg; break;
        case 'x': forbidden = optarg; break;
        case 'c': create = 1; break;
        case 'I': install = optarg; break;
        case 'n': no_replace = 1; break;
        case 'V': printf("qnx-mcp-run %s\n", QMR_VERSION); return 0;
        case 'l': list_dir = optarg; break;
        case 'Z': stop_pid = num_arg(optarg, 0, LONG_MAX); break;
        case 'q': expect = optarg; break;
        case 's': signame = optarg; break;
        case 'f': force = 1; break;
        case 'A':
        case 'F':
            if ((opt == 'A' ? nroots : nforbid) == MAX_ROOTS || optarg[0] != '/') {
                fail("bad -A or -F", 0);
            }
            if (opt == 'A') {
                roots[nroots++] = optarg;
            } else {
                forbid[nforbid++] = optarg;
            }
            break;
        default: fail("unknown option", 0);
        }
    }
    if (hash_path != NULL) {
        return mode_hash(hash_path, forbidden);
    }
    if (list_dir != NULL) {
        return mode_list(list_dir, max_chunk);
    }
    if (stop_pid != 0) {
        return mode_pstop(stop_pid, expect, signame, o.core_wait_ms, force, roots, nroots, forbid, nforbid);
    }
    if (cat_path != NULL) {
        return mode_cat(cat_path, max_chunk);
    }
    if (poll_dir != NULL) {
        return mode_poll(poll_dir, out_off, err_off, max_chunk);
    }
    if (stop_dir != NULL) {
        return mode_stop(stop_dir, o.core_wait_ms);
    }
    if (prune_dir != NULL) {
        return mode_prune(prune_dir, keep, age_s, protect, nprotect);
    }
    if (dest != NULL) {
        if (root == NULL) {
            fail("-D needs -r root", 0);
        }
        return mode_dest(root, dest, forbidden, create, install, no_replace);
    }
    if (optind >= argc) {
        fail("missing program", 0);
    }
    o.prog = &argv[optind];
    if (forbidden != NULL) {
        check_program(o.prog[0], forbidden); /* checked again here, just before the program starts */
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop;
    if (sigaction(SIGHUP, &sa, NULL) == -1 || sigaction(SIGTERM, &sa, NULL) == -1 || sigaction(SIGINT, &sa, NULL) == -1) {
        fail("sigaction", errno);
    }
    sa.sa_handler = on_deadline;
    if (sigaction(SIGALRM, &sa, NULL) == -1) {
        fail("sigaction", errno);
    }
    signal(SIGPIPE, SIG_IGN);

    if (bg_dir != NULL) {
        o.watch_stdin = 0;
        return mode_background(&o, bg_dir);
    }

    int active = -1;
    if (o.core_dir != NULL) {
        /* While this lock is held, pruning never removes the run's directory. */
        char p[PATH_MAX];
        snprintf(p, sizeof p, "%s/active", o.core_dir);
        active = hold_lock(p);
        if (active == -1) {
            warn_errno("lock run directory", errno);
        }
    }
    struct sink out = {-1, malloc(o.max_bytes + 1), -1, o.max_bytes, 0, 0, 0};
    struct sink err = {-1, malloc(o.max_bytes + 1), -1, o.max_bytes, 0, 0, 0};
    if (out.buf == NULL || err.buf == NULL) {
        fail("out of memory", ENOMEM);
    }
    struct result r;
    run_program(&o, &r, &out, &err, -1);
    print_result(stdout, &r, &out, &err, 1);
    return 0;
}
