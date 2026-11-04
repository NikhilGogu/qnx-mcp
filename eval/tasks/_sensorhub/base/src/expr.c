/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "expr.h"
#include "log.h"
#include "registry.h"
#include "util.h"

/* Derived channels: name = expression, where an expression is a number, a
 * sensor name, an earlier derived channel or fn(expr, ...). Channels are
 * evaluated in file order once per cycle. */
#define MAX_ARGS 8
#define MAX_NEST 200

enum { N_NUM, N_SENSOR, N_DERIVED, N_CALL };
enum { FN_SUM, FN_AVG, FN_MIN, FN_MAX, FN_DIFF, FN_SCALE };

struct node {
    int kind;
    double num;
    const struct sensor *sensor;
    int chan;
    int fn;
    int nargs;
    struct node *args[MAX_ARGS];
};

struct parser {
    const char *p;
    const char *err;
    int depth;
};

static const char *const fn_names[] = { "sum", "avg", "min", "max", "diff", "scale" };
static struct derived *chans;
static int chans_cap;
static int nchans;
static char **pending;
static int pending_cap;
static int npending;
static int parsed;
static struct worker dw;

static void skip_ws(struct parser *ps)
{
    while (isspace((unsigned char)*ps->p))
        ps->p++;
}

static struct node *new_node(int kind)
{
    struct node *n = calloc(1, sizeof *n);

    if (n)
        n->kind = kind;
    return n;
}

static struct node *parse_expr(struct parser *ps);

static int find_chan(const char *name)
{
    for (int i = 0; i < nchans; i++)
        if (strcmp(chans[i].name, name) == 0)
            return i;
    return -1;
}

static struct node *parse_call(struct parser *ps, const char *fname)
{
    struct node *n;
    int fn = -1;

    for (int i = 0; i < (int)(sizeof fn_names / sizeof fn_names[0]); i++)
        if (strcmp(fn_names[i], fname) == 0)
            fn = i;
    if (fn < 0) {
        ps->err = "unknown function";
        return NULL;
    }
    n = new_node(N_CALL);
    if (n == NULL)
        return NULL;
    n->fn = fn;
    ps->p++;
    for (;;) {
        struct node *arg;

        skip_ws(ps);
        if (n->nargs == MAX_ARGS) {
            ps->err = "too many arguments";
            return NULL;
        }
        arg = parse_expr(ps);
        if (arg == NULL)
            return NULL;
        n->args[n->nargs++] = arg;
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == ')') {
            ps->p++;
            break;
        }
        ps->err = "expected , or )";
        return NULL;
    }
    if ((fn == FN_DIFF || fn == FN_SCALE) && n->nargs != 2) {
        ps->err = "diff and scale take two arguments";
        return NULL;
    }
    return n;
}

static struct node *parse_expr(struct parser *ps)
{
    char tok[96];
    size_t len = 0;
    struct node *n;
    int k;

    if (++ps->depth > MAX_NEST) {
        ps->err = "nested too deeply";
        return NULL;
    }
    skip_ws(ps);
    if (isdigit((unsigned char)*ps->p) || *ps->p == '-' || *ps->p == '.') {
        char *end;

        n = new_node(N_NUM);
        if (n == NULL)
            return NULL;
        n->num = strtod(ps->p, &end);
        if (end == ps->p) {
            ps->err = "bad number";
            return NULL;
        }
        ps->p = end;
        ps->depth--;
        return n;
    }
    while ((isalnum((unsigned char)*ps->p) || *ps->p == '_') && len < sizeof tok - 1)
        tok[len++] = *ps->p++;
    tok[len] = '\0';
    if (len == 0) {
        ps->err = "expected a name";
        return NULL;
    }
    skip_ws(ps);
    if (*ps->p == '(') {
        n = parse_call(ps, tok);
        ps->depth--;
        return n;
    }
    if ((k = find_chan(tok)) >= 0) {
        n = new_node(N_DERIVED);
        if (n)
            n->chan = k;
        ps->depth--;
        return n;
    }
    n = new_node(N_SENSOR);
    if (n == NULL)
        return NULL;
    n->sensor = registry_find(tok);
    if (n->sensor == NULL) {
        ps->err = "unknown sensor or channel";
        return NULL;
    }
    ps->depth--;
    return n;
}

static double eval(const struct node *n)
{
    double acc, v;

    switch (n->kind) {
    case N_NUM:
        return n->num;
    case N_SENSOR:
        return n->sensor->st.last;
    case N_DERIVED:
        return chans[n->chan].value;
    }
    acc = eval(n->args[0]);
    for (int i = 1; i < n->nargs; i++) {
        v = eval(n->args[i]);
        switch (n->fn) {
        case FN_SUM:
        case FN_AVG:
            acc += v;
            break;
        case FN_MIN:
            if (v < acc)
                acc = v;
            break;
        case FN_MAX:
            if (v > acc)
                acc = v;
            break;
        case FN_DIFF:
            acc -= v;
            break;
        case FN_SCALE:
            acc *= v;
            break;
        }
    }
    return n->fn == FN_AVG ? acc / n->nargs : acc;
}

static void parse_pending(void)
{
    for (int i = 0; i < npending; i++) {
        char *line = pending[i], *key, *val;
        struct parser ps;

        if (split_kv(line, &key, &val) == 1 && strlen(key) < sizeof chans[0].name) {
            if (nchans == chans_cap) {
                struct derived *more = realloc(chans, (size_t)(chans_cap ? chans_cap * 2 : 64) * sizeof *chans);

                if (more == NULL)
                    break;
                chans = more;
                chans_cap = chans_cap ? chans_cap * 2 : 64;
            }
            memset(&chans[nchans], 0, sizeof chans[0]);
            ps.p = val;
            ps.err = NULL;
            ps.depth = 0;
            chans[nchans].root = parse_expr(&ps);
            skip_ws(&ps);
            if (chans[nchans].root != NULL && *ps.p == '\0') {
                copy_str(chans[nchans].name, sizeof chans[nchans].name, key);
                nchans++;
            } else {
                log_msg(LOG_WARN, "derived.cfg: %s rejected: %s", key, ps.err ? ps.err : "trailing text");
            }
        }
        free(line);
    }
    npending = 0;
    parsed = 1;
}

static int derive_step(void *ctx, int cycle)
{
    (void)ctx;
    (void)cycle;
    if (!parsed)
        parse_pending();
    for (int i = 0; i < nchans; i++) {
        chans[i].value = eval(chans[i].root);
        chans[i].valid = 1;
    }
    return 0;
}

int derive_setup(const char *dir, struct worker **out)
{
    char path[512];
    char *line = NULL;
    size_t cap = 0;
    FILE *f;

    snprintf(path, sizeof path, "%s/derived.cfg", dir);
    f = fopen(path, "r");
    if (f == NULL)
        return 0;
    while (getline(&line, &cap, f) > 0) {
        char *t = str_trim(line);

        if (*t == '\0' || *t == '#')
            continue;
        if (npending == pending_cap) {
            char **more = realloc(pending, (size_t)(pending_cap ? pending_cap * 2 : 64) * sizeof *pending);

            if (more == NULL)
                break;
            pending = more;
            pending_cap = pending_cap ? pending_cap * 2 : 64;
        }
        pending[npending++] = strdup(t);
    }
    free(line);
    fclose(f);
    if (npending == 0)
        return 0;
    dw.name = "derive";
    dw.step = derive_step;
    if (worker_start(&dw, 0) != 0)
        return -1;
    out[0] = &dw;
    return 1;
}

int derive_count(void)
{
    return nchans;
}

const struct derived *derive_get(int i)
{
    return &chans[i];
}
