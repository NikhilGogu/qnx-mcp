# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Proof test: a core and a binary that do not belong together.

Baseline: plain `ntox86_64-gdb -batch -ex bt <binary> <newest core>` with the binary on the host now.
Ours: `get_crash` with the same core and the same binary.

Eight cases, two crash programs times four situations:
  rebuilt_after_crash  the program crashed, then the source was edited and rebuilt; the host binary is the new build
  two_crashes          the program crashed, then another program crashed; the newest core is the other one's
  leftover_old_core    the program crashed, then a clean -O2 rebuild ran without crashing; the newest core is still the old one
  no_build_id          like rebuilt_after_crash, but both builds are linked with --build-id=none

Truth is gdb with the binary that really crashed. A baseline result is "correct" when gdb prints the same stack, "wrong_named" when it prints a
stack with at least one named function that differs from the truth, and "unreadable" when it names no function at all
(every frame is "?? ()"). Only wrong_named is a plausible but wrong backtrace; top_frame_named says whether even frame 0 was named. gdb's own
"may not match" warnings are recorded. For ours a case counts as refused when get_crash returns core_mismatch.
Two controls show that get_crash is not simply refusing everything: the right binary with the same core, and
the normal path (deploy_and_run, then get_crash by run_id).

Needs QNX_MCP_E2E_TARGET (a running target made by target_create, logged in as root, dumper running with
`-d /data/var/dumper`) and the QNX SDP. QNX_MCP_PROOF_ROUNDS (default 1) repeats the eight cases;
QNX_MCP_PROOF_OUT, if set, is a directory that receives stale_core_backtrace.json. See docs/PROOF_TESTS.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from helpers import mcp_client, run_async
from qnx_mcp.sdk import sdk_env, sdk_tool
from qnx_mcp.target import Target, gdb_for
from qnx_mcp.util import Config

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
DUMPER_DIR = "/data/var/dumper"
ROUNDS = int(os.environ.get("QNX_MCP_PROOF_ROUNDS", "1"))  # how often the eight cases are repeated (gdb's output depends on where ASLR put the programs)

# Program 1: a table that was never filled in, read two calls below main. Crashes only with the argument "crash".
NULL_V1 = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int *table;
static int lookup(int i)
{
    return table[i];
}
static int process(int n)
{
    int sum = 0;
    for (int i = 0; i < n; i++)
        sum += lookup(i);
    return sum;
}
int main(int argc, char **argv)
{
    int n = argc > 1 && !strcmp(argv[1], "crash") ? 4 : 0;
    printf("n=%d\n", n);
    fflush(stdout);
    return process(n) != 0;
}
'''
# The same program after an ordinary edit: a banner, two helpers and a changed main.
NULL_V2 = r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void banner(const char *s)
{
    printf("== %s ==\n", s);
    fflush(stdout);
}
static int clamp(int v, int lo, int hi)
{
    if (v < lo)
        return lo;
    return v > hi ? hi : v;
}
static unsigned checksum(const char *s)
{
    unsigned h = 5381;
    while (*s)
        h = h * 33 + (unsigned char)*s++;
    return h;
}
static int *table;
static int lookup(int i)
{
    return table[i];
}
static int process(int n)
{
    int sum = 0;
    for (int i = 0; i < clamp(n, 0, 8); i++)
        sum += lookup(i);
    return sum;
}
int main(int argc, char **argv)
{
    int n = argc > 1 && !strcmp(argv[1], "crash") ? 4 : 0;
    banner("table check");
    printf("n=%d id=%u\n", n, checksum("table"));
    fflush(stdout);
    return process(n) != 0;
}
'''
# Program 2: a division by zero (SIGFPE) one call below the loop in main.
DIV_V1 = r'''#include <stdio.h>
#include <string.h>
static int scale(int total, int count)
{
    return total / count;
}
static int average(const int *v, int n, int groups)
{
    int total = 0;
    for (int i = 0; i < n; i++)
        total += v[i];
    return scale(total, groups);
}
int main(int argc, char **argv)
{
    int v[4] = {3, 5, 7, 9};
    int groups = argc > 1 && !strcmp(argv[1], "crash") ? 0 : 2;
    printf("groups=%d\n", groups);
    fflush(stdout);
    return average(v, 4, groups) != 0;
}
'''
DIV_V2 = r'''#include <stdio.h>
#include <string.h>
static void trace(const char *what, int value)
{
    printf("%s=%d\n", what, value);
    fflush(stdout);
}
static int max_of(const int *v, int n)
{
    int m = v[0];
    for (int i = 1; i < n; i++)
        if (v[i] > m)
            m = v[i];
    return m;
}
static int min_of(const int *v, int n)
{
    int m = v[0];
    for (int i = 1; i < n; i++)
        if (v[i] < m)
            m = v[i];
    return m;
}
static int scale(int total, int count)
{
    return total / count;
}
static int average(const int *v, int n, int groups)
{
    int total = 0;
    for (int i = 0; i < n; i++)
        total += v[i];
    trace("max", max_of(v, n));
    trace("min", min_of(v, n));
    return scale(total, groups);
}
int main(int argc, char **argv)
{
    int v[4] = {3, 5, 7, 9};
    int groups = argc > 1 && !strcmp(argv[1], "crash") ? 0 : 2;
    printf("groups=%d\n", groups);
    fflush(stdout);
    return average(v, 4, groups) != 0;
}
'''
# Another program that crashes later (for two_crashes).
OTHER = r'''#include <signal.h>
#include <stdio.h>
static void boom(int depth)
{
    if (depth == 0)
        raise(SIGSEGV);
    else
        boom(depth - 1);
}
int main(void)
{
    puts("other");
    fflush(stdout);
    boom(3);
    return 0;
}
'''
PROGRAMS = {"null": (NULL_V1, NULL_V2), "div": (DIV_V1, DIV_V2)}
SITUATIONS = ("rebuilt_after_crash", "two_crashes", "leftover_old_core", "no_build_id")
FRAME_RE = re.compile(r"^#(\d+)\s+(?:0x[0-9a-f]+ in )?([A-Za-z_][A-Za-z0-9_.]*) \(.*?\)(?: at (\S+?):(\d+))?\s*$", re.M)


def _stack(text: str) -> list[tuple[str, int | None]]:
    """(function, line) of every frame the `bt` command printed (the part after the marker), down to main."""
    out: list[tuple[str, int | None]] = []
    for m in FRAME_RE.finditer(text.split("@@BT", 1)[-1]):
        out.append((m.group(2), int(m.group(4)) if m.group(4) else None))
        if m.group(2) == "main":
            break
    return out


def _gdb(cfg: Config, binary: str, core: str) -> dict[str, Any]:
    """Plain gdb, as a developer would run it. The sysroot lines only let it find libc; they do not check the core."""
    env = sdk_env(cfg)
    sysroot = os.path.join(env["QNX_TARGET"], "x86_64")
    libpath = ":".join(os.path.join(sysroot, p) for p in ("lib", "usr/lib", "lib/dll"))
    argv = [sdk_tool(env, gdb_for("x86_64")), "-q", "-nx", "-batch", "-iex", f"set sysroot {sysroot}", "-iex", f"set solib-search-path {libpath}",
            "-iex", "set auto-load off", "-ex", "echo @@BT\\n", "-ex", "bt", binary, core]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env)
    text = r.stdout + "\n" + r.stderr
    warnings = sorted({ln.strip() for ln in text.splitlines() if ln.lstrip().startswith("warning:") and "Can't open file" not in ln})
    raw_bt = r.stdout.split("@@BT", 1)[-1].strip()
    return {"stack": _stack(r.stdout), "warnings": warnings, "bt_raw": raw_bt[:1500]}


def _cores(t: Target) -> list[str]:
    """Core files in dumper's directory, oldest first."""
    out = t.ssh(["sh", "-c", f"ls -tr {DUMPER_DIR}/*.core 2>/dev/null; true"], timeout=30, check=False).out
    return [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".core")]


def _crash_plain(t: Target, opts: list[str], ip: str, remote: str, arg: str | None) -> str | None:
    """Run a program with plain ssh; return the new core dumper wrote (None when there is none)."""
    before = set(_cores(t))
    subprocess.run(["ssh", *opts, f"root@{ip}", remote + (f" {arg}" if arg else "")], capture_output=True, timeout=60)
    new: list[str] = []
    for _ in range(40):
        new = [c for c in _cores(t) if c not in before]
        if new:
            break
        time.sleep(0.5)
    if not new:
        return None
    last = -1
    for _ in range(40):  # wait until dumper has finished writing
        size = int(t.ssh(["sh", "-c", f"wc -c < {new[-1]}"], timeout=30).out.split()[0])
        if size == last and size > 0:
            break
        last = size
        time.sleep(0.5)
    return new[-1]


def test_stale_core_backtrace(tmp_path: Path):
    cfg = Config.from_env()
    t = Target(cfg, TARGET)
    ip = t.require_ip()
    opts = t._ssh_opts()
    suffix = uuid.uuid4().hex[:6]
    remote_dir = f"/data/var/tmp/proof-stale-{suffix}"
    local: dict[str, str] = {}
    cases: list[dict[str, Any]] = []

    async def build_all() -> None:
        async with mcp_client({}) as c:
            async def build(key: str, src: str, **kw: Any) -> None:
                f = tmp_path / f"{key}_{suffix}.c"
                f.write_text(src)
                err, b = await c.call("build", path=str(f), output=str(tmp_path / f"{key}_{suffix}"), **kw)
                assert not err and b["artifacts"], b
                art = b["artifacts"][0]
                local[key] = art["path"]
                local[key + ".build_id"] = art["build_id"] or ""

            await build("other", OTHER)
            for prog, (v1, v2) in PROGRAMS.items():
                await build(f"{prog}_v1", v1, cflags=["-g", "-O0"])
                await build(f"{prog}_v2", v2, cflags=["-g", "-O0"])
                await build(f"{prog}_v1_o2", v1, cflags=["-g", "-O2"])
                await build(f"{prog}_v1_nobid", v1, cflags=["-g", "-O0"], ldflags=["-Wl,--build-id=none"])
                await build(f"{prog}_v2_nobid", v2, cflags=["-g", "-O0"], ldflags=["-Wl,--build-id=none"])

    async def ours(core: str | None, binary: str, run_binary: str) -> dict[str, Any]:
        """get_crash with the baseline's inputs, then the two controls."""
        res: dict[str, Any] = {}
        async with mcp_client({}) as c:
            if core:
                err, r = await c.call("get_crash", target=TARGET, core_path=core, binary=binary)
                prov = r.get("provenance") or (r.get("error") or {}).get("details", {}).get("provenance") or {}
                res["same_inputs"] = {"error_code": (r.get("error") or {}).get("code"), "verified": prov.get("verified"),
                                      "frames_printed": len(r.get("frames") or []), "stack": [(f.get("function"), f.get("line")) for f in r.get("frames") or []][:12]}
            # control B: the normal path, a run made by qnx-mcp and get_crash by run_id
            err, run = await c.call("deploy_and_run", target=TARGET, binary=run_binary, args=["crash"], timeout_s=30)
            assert not err and run["outcome"] == "crashed" and run.get("core_path"), run
            err, cr = await c.call("get_crash", target=TARGET, run_id=run["run_id"])
            res["run_id"] = run["run_id"]
            res["by_run_id"] = {"run_core_path": run.get("core_path"), "error": (cr.get("error") or {}).get("message"),
                                "error_code": (cr.get("error") or {}).get("code"), "verified": (cr.get("provenance") or {}).get("verified"),
                                "stack": [(f.get("function"), f.get("line")) for f in cr.get("frames") or []][:12]}
        return res

    async def control_right_binary(core: str, binary: str) -> dict[str, Any]:
        async with mcp_client({}) as c:
            err, r = await c.call("get_crash", target=TARGET, core_path=core, binary=binary)
            return {"error_code": (r.get("error") or {}).get("code"), "verified": (r.get("provenance") or {}).get("verified"),
                    "stack": [(f.get("function"), f.get("line")) for f in r.get("frames") or []][:12]}

    run_async(build_all())
    t.ssh(["mkdir", "-p", remote_dir], timeout=30)

    def deploy(key: str) -> str:
        dest = f"{remote_dir}/{uuid.uuid4().hex[:4]}-{Path(local[key]).name}"  # a new path each time: the last copy may still be open in dumper
        t.put(Path(local[key]), dest, timeout=120)
        t.ssh(["chmod", "755", dest], timeout=30)
        return dest

    def fetch(core: str, name: str) -> str:
        dest = tmp_path / name
        subprocess.run(["scp", *opts, f"root@{ip}:{core}", str(dest)], capture_output=True, check=True, timeout=300)
        return str(dest)

    try:
        for rnd in range(ROUNDS):
            for prog in PROGRAMS:
                for sit in SITUATIONS:
                    label = f"{prog}/{sit}" + (f"#{rnd + 1}" if ROUNDS > 1 else "")
                    # The image's /data is small (64 MB) and every core is about 1.6 MB: remove the cores of the earlier cases first.
                    t.ssh(["sh", "-c", f"rm -f {DUMPER_DIR}/*.core"], timeout=60)
                    nobid = sit == "no_build_id"
                    crashed_key = f"{prog}_v1_nobid" if nobid else f"{prog}_v1"
                    # host binary at debug time
                    host_key = {"rebuilt_after_crash": f"{prog}_v2", "two_crashes": f"{prog}_v1", "leftover_old_core": f"{prog}_v1_o2",
                                "no_build_id": f"{prog}_v2_nobid"}[sit]
                    core_a = _crash_plain(t, opts, ip, deploy(crashed_key), "crash")
                    assert core_a, f"{label}: the crashing program left no core"
                    newest = core_a
                    if sit == "two_crashes":
                        newest = _crash_plain(t, opts, ip, deploy("other"), None) or ""
                        assert newest and newest != core_a, f"{label}: the second crash left no core"
                    elif sit == "leftover_old_core":
                        assert _crash_plain(t, opts, ip, deploy(host_key), None) is None, f"{label}: the clean run left a core"
                    assert _cores(t)[-1] == newest, f"{label}: newest core is not the intended one"
                    core_a_local = fetch(core_a, f"{prog}_{sit}_a.core")
                    newest_local = core_a_local if newest == core_a else fetch(newest, f"{prog}_{sit}_newest.core")
                    truth = _gdb(cfg, local[crashed_key], core_a_local)
                    base = _gdb(cfg, local[host_key], newest_local)  # the baseline: plain gdb, newest core, binary on the host now
                    same_stack = base["stack"] == truth["stack"] and bool(base["stack"])
                    # correct: the crash's stack; wrong_named: a stack whose top frame has a function name but is not the crash's;
                    # unreadable: no frames, or a top frame gdb could not name ("?? ()")
                    base["top_frame_named"] = bool(base["bt_raw"]) and "in ?? ()" not in base["bt_raw"].splitlines()[0]
                    base["verdict"] = "unreadable" if not base["stack"] else "correct" if same_stack else "wrong_named"
                    o = run_async(ours(newest, local[host_key], local[crashed_key]))
                    t.ssh(["rm", "-rf", f"/tmp/qnx-mcp/runs/{o.pop('run_id')}"], timeout=60)  # the control run's own core
                    o["control_right_binary"] = None if nobid else run_async(control_right_binary(core_a, local[crashed_key]))
                    si = o["same_inputs"]
                    si["verdict"] = ("refused" if si["error_code"] == "core_mismatch" else "other_error" if si["error_code"]
                                     else "printed_unverified" if si["verified"] is False else "printed_verified")
                    if si["verdict"].startswith("printed"):
                        si["verdict"] += "_correct" if si["stack"][:len(truth["stack"])] == truth["stack"] else "_wrong"
                    o["by_run_id"]["verdict"] = "correct" if o["by_run_id"]["verified"] is True and o["by_run_id"]["stack"][:len(truth["stack"])] == truth["stack"] else "not_correct"
                    cases.append({"case": label, "crashed_binary": Path(local[crashed_key]).name, "host_binary": Path(local[host_key]).name,
                                  "crashed_build_id": local[crashed_key + ".build_id"], "host_build_id": local[host_key + ".build_id"],
                                  "truth_stack": truth["stack"], "baseline_gdb": base, "qnx_mcp_get_crash": o})
                    print(f"\n{label}: gdb {base['verdict']} {base['stack'][:3]} warnings={base['warnings']}; get_crash {si['verdict']} {si['error_code']}")
    finally:
        t.ssh(["sh", "-c", f"rm -rf {remote_dir}; rm -f {DUMPER_DIR}/*.core"], timeout=60, check=False)
        n = len(cases)
        summary = {
            "cases": n,
            "gdb_wrong_named": sum(c["baseline_gdb"]["verdict"] == "wrong_named" for c in cases),
            "gdb_correct": sum(c["baseline_gdb"]["verdict"] == "correct" for c in cases),
            "gdb_unreadable": sum(c["baseline_gdb"]["verdict"] == "unreadable" for c in cases),
            "gdb_not_correct_with_may_not_match_warning": sum(c["baseline_gdb"]["verdict"] != "correct" and any("may not match" in w for w in c["baseline_gdb"]["warnings"])
                                                              for c in cases),
            "get_crash_refused_core_mismatch": sum(c["qnx_mcp_get_crash"]["same_inputs"]["verdict"] == "refused" for c in cases),
            "get_crash_printed_wrong": sum(c["qnx_mcp_get_crash"]["same_inputs"]["verdict"].endswith("_wrong") for c in cases),
            "get_crash_printed_unverified": sum(c["qnx_mcp_get_crash"]["same_inputs"]["verdict"].startswith("printed_unverified") for c in cases),
            "gdb_by_situation": {sit: {v: sum(c["case"].split("/")[1].split("#")[0] == sit and c["baseline_gdb"]["verdict"] == v for c in cases)
                                       for v in ("correct", "wrong_named", "unreadable")} for sit in SITUATIONS},
            "by_run_id_correct": sum(c["qnx_mcp_get_crash"]["by_run_id"]["verdict"] == "correct" for c in cases),
        }
        result = {"summary": summary, "cases": cases}
        out = os.environ.get("QNX_MCP_PROOF_OUT")
        if out and cases:
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out) / "stale_core_backtrace.json").write_text(json.dumps(result, indent=1) + "\n")
        print("\nPROOF stale_core_backtrace " + json.dumps(summary))

    # The claim: gdb prints a stack that is not the crash's in k of N cases; get_crash prints none of those.
    assert summary["cases"] == ROUNDS * len(PROGRAMS) * len(SITUATIONS)
    assert summary["gdb_correct"] < summary["cases"] and summary["gdb_wrong_named"] >= 1, summary
    assert summary["get_crash_printed_wrong"] == 0, summary
    assert summary["get_crash_refused_core_mismatch"] == summary["cases"], summary
    # Controls: get_crash is not just refusing everything.
    assert summary["by_run_id_correct"] == summary["cases"], summary
    assert all(c["qnx_mcp_get_crash"]["control_right_binary"]["verified"] is True
               and c["qnx_mcp_get_crash"]["control_right_binary"]["stack"][:len(c["truth_stack"])] == c["truth_stack"]
               for c in cases if c["qnx_mcp_get_crash"]["control_right_binary"]), cases
