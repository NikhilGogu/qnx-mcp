# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Vertical slice through MCP over stdio against a real QNX 8.0 target.

build -> deploy_and_run (crashes) -> get_crash with provenance, then the runner and safety
checks (arguments without a shell, timeout, descendants, cancellation), threads, background
runs, file transfer, processes and logs. Set QNX_MCP_E2E_TARGET=<running target name>.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from helpers import mcp_client, run_async
from qnx_mcp import ops
from qnx_mcp.target import Target
from qnx_mcp.util import Config, QnxMcpError

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
DEMO = Path(__file__).resolve().parents[1] / "examples" / "crash_demo"
STATE = Path(os.environ.get("QNX_MCP_STATE_DIR", str(Path.home() / ".qnx-mcp" / "state")))

ECHOARGS = '#include <stdio.h>\nint main(int c, char **v){ for (int i = 1; i < c; i++) printf("[%s]\\n", v[i]); return 7; }\n'
HANG = '#include <unistd.h>\n#include <stdio.h>\nint main(void){ puts("start"); fflush(stdout); for (;;) sleep(1); }\n'
FORKER = r'''#include <stdio.h>
#include <unistd.h>
int main(void)
{
    pid_t child = fork();
    if (child == 0) { sleep(30); return 0; }   /* a descendant left behind in the process group */
    printf("%d\n", (int)child);
    return 0;
}
'''
THREADS = r'''#include <pthread.h>
#include <stdio.h>
#include <unistd.h>
static pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;
static void *waiter(void *a) { (void)a; pthread_mutex_lock(&m); pthread_mutex_unlock(&m); return NULL; }
static void *crasher(void *a) { (void)a; usleep(200000); volatile int *p = NULL; return (void *)(long)*p; }
int main(void)
{
    pthread_t w, c;
    pthread_mutex_lock(&m);
    pthread_create(&w, NULL, waiter, NULL);
    pthread_create(&c, NULL, crasher, NULL);
    pthread_join(c, NULL);
    return 0;
}
'''
TICKER = r'''#include <stdio.h>
#include <unistd.h>
int main(void)
{
    for (int i = 0;; i++) { printf("tick %d\n", i); fflush(stdout); usleep(200000); }
}
'''


def _crash_line() -> int:
    for n, line in enumerate((DEMO / "crash.c").read_text().splitlines(), 1):
        if "CRASH-LINE" in line:
            return n
    raise AssertionError("marker missing")


def _show(title: str, data: object) -> None:
    print(f"\n--- {title}\n{json.dumps(data, indent=1)[:3000]}")


async def _build(c, tmp_path: Path, name: str, src: str, **kw) -> str:
    (tmp_path / f"{name}.c").write_text(src)
    err, b = await c.call("build", path=str(tmp_path / f"{name}.c"), **kw)
    assert not err and b["artifacts"], b
    return b["artifacts"][0]["path"]


def test_crash_slice(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c:
            err, st = await c.call("target_status", name=TARGET)
            assert not err and st["state"] == "running" and st["dumper_running"] is True and st["host_key_policy"] == "image", st
            _show("target_status", {k: st.get(k) for k in ("name", "state", "ip", "uname", "dumper_running", "dumper_args", "core_naming", "host_key_policy")})

            exe = tmp_path / "crash_demo"
            err, b = await c.call("build", path=str(DEMO), output=str(exe))
            assert not err and b["ok"] and b["error_count"] == 0, b
            art = b["artifacts"][0]
            assert art["arch"] == "x86_64" and art["qnx"] is True and art["debug_info"] is True and art["build_id"], art
            _show("build", {"ok": b["ok"], "argv": b["steps"][0]["argv"], "artifact": art})

            err, r = await c.call("deploy_and_run", target=TARGET, binary=art["path"], timeout_s=20)
            assert not err and r["outcome"] == "crashed" and r["signal_name"] == "SIGSEGV", r
            assert r["dumper"] == "notified" and r["core_path"] == f"/tmp/qnx-mcp/runs/{r['run_id']}/crash_demo.core", r
            assert r["dumper_core_path"].startswith("/data/var/dumper/") and r["group_clean"] and r["capture_complete"], r
            assert 0 <= r["core_ready_ms"] < 5000, r
            assert "imu: average offset 6" in r["stdout"] and r["binary_sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest()
            _show("deploy_and_run", {k: r[k] for k in ("run_id", "outcome", "signal_name", "stdout", "dumper", "core_path", "dumper_core_path",
                                                       "remote_path", "duration_ms", "core_ready_ms", "group_clean", "next_step")})

            # Rebuild the same output path with different code: get_crash must still use the deployed snapshot.
            err, b2 = await c.call("build", path=str(DEMO), output=str(exe), cflags=["-g", "-O0", "-DREBUILT=1", "-fno-inline"])
            assert not err and b2["artifacts"][0]["build_id"] != art["build_id"], b2

            err, cr = await c.call("get_crash", target=TARGET, run_id=r["run_id"])
            assert not err, cr
            ff, uf = cr["faulting_frame"], cr["user_frame"]
            assert (ff["function"], Path(ff["file"]).name, ff["line"]) == ("read_calibration", "crash.c", _crash_line()), ff
            assert uf == ff and cr["fault_in_user_code"] is True and ff["fullname"] == str(DEMO / "crash.c")
            assert [f["function"] for f in cr["frames"][:3]] == ["read_calibration", "average_offset", "main"]
            assert cr["fault_address"] == "0x0" and cr["signal_name"] == "SIGSEGV" and cr["si_code_name"].startswith("SEGV_MAPERR")
            assert any(v["name"] == "s" for v in ff["locals"]) and any(v["name"] == "index" for v in ff["locals"])
            pv = cr["provenance"]
            assert pv["verified"] and pv["build_id_match"] is True and pv["executable_match"] is True and pv["pid_match"] is True, pv
            assert pv["core_build_id"] == art["build_id"] and pv["core_executable"] == r["remote_path"] and pv["core_pid"] == r["pid"], pv
            assert pv["binary_sha256"] == r["binary_sha256"] and pv["binary_build_id"] == art["build_id"], pv
            assert sum(t["faulting"] for t in cr["threads"]) == 1 and all(t.get("frames") for t in cr["threads"]), cr["threads"]
            _show("get_crash", {"summary": cr["summary"], "fault_address": cr["fault_address"], "faulting_instruction": cr["faulting_instruction"],
                                "frames": [{k: f[k] for k in ("level", "function", "file", "line", "address")} for f in cr["frames"]],
                                "crash_line_source": [s for s in ff["source"] if s["current"]], "locals_frame0": ff["locals"],
                                "provenance": pv, "threads": cr["threads"]})

            # The rebuilt binary does not match the core: refused.
            err, mm = await c.call("get_crash", target=TARGET, core_path=r["core_path"], binary=str(exe))
            assert err and mm["error"]["code"] == "core_mismatch", mm
            _show("get_crash with a rebuilt binary", mm)
            # A binary without a build ID cannot be matched to a core outside a recorded run.
            err, nb = await c.call("build", path=str(DEMO), output=str(tmp_path / "nobid"), ldflags=["-Wl,--build-id=none"])
            assert not err and nb["artifacts"][0]["build_id"] is None, nb
            err, mm2 = await c.call("get_crash", target=TARGET, core_path=r["core_path"], binary=nb["artifacts"][0]["path"])
            assert err and mm2["error"]["code"] == "core_mismatch" and "no GNU build ID" in mm2["error"]["message"], mm2
            # A modified snapshot of the deployed binary is refused.
            snap = Path(r["binary_snapshot"]) if "binary_snapshot" in r else next((STATE / "targets" / TARGET / "runs" / r["run_id"] / "bin").iterdir())
            orig = snap.read_bytes()
            snap.write_bytes(orig + b"\0")
            err, tam = await c.call("get_crash", target=TARGET, run_id=r["run_id"])
            snap.write_bytes(orig)
            assert err and tam["error"]["code"] == "core_mismatch" and "snapshot" in tam["error"]["message"], tam

            err, ok_run = await c.call("deploy_and_run", target=TARGET, binary=art["path"], args=["0"], timeout_s=20)
            assert not err and ok_run["outcome"] == "exited" and ok_run["exit_code"] == 0 and ok_run["core_path"] is None, ok_run

            # Compiler diagnostics as objects.
            (tmp_path / "bad.c").write_text("int f(int x)\n{\n    int unused;\n    return x + y;\n}\n")
            err, bb = await c.call("build", path=str(tmp_path / "bad.c"))
            assert err and bb["ok"] is False and bb["error"]["code"] == "build_failed", bb
            e0 = next(d for d in bb["diagnostics"] if d["severity"] == "error")
            assert (Path(e0["file"]).name, e0["line"], e0["column"]) == ("bad.c", 4, 16) and "'y' undeclared" in e0["message"], e0
            w0 = next(d for d in bb["diagnostics"] if d["severity"] == "warning")
            assert w0["option"] == "-Wunused-variable" and w0["line"] == 3
            _show("build bad.c (diagnostics)", bb["diagnostics"])

    run_async(go())
    print("\n--- MCP call log\n" + "\n".join(log))


def test_runner_contract(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c:
            # Arguments arrive verbatim: no remote shell expansion.
            echo = await _build(c, tmp_path, "echoargs", ECHOARGS)
            evil = ["$(touch /tmp/qnx-mcp-pwned)", "a b; touch /tmp/qnx-mcp-pwned", "`id`", "it's", "*", "line1\nline2", " sep"]
            err, er = await c.call("deploy_and_run", target=TARGET, binary=echo, args=evil)
            assert not err and er["exit_code"] == 7 and er["stdout"] == "".join(f"[{a}]\n" for a in evil), er
            _show("deploy_and_run echoargs (injection attempt)", {k: er[k] for k in ("outcome", "exit_code", "stdout")})

            # Output cap is reported.
            err, big = await c.call("deploy_and_run", target=TARGET, binary=echo, args=["x" * 3000], max_output_kb=1)
            assert not err and big["stdout_truncated"] and big["stdout_bytes"] == 3003 and len(big["stdout"]) == 1024, big

            # Timeout kills the program.
            hang = await _build(c, tmp_path, "hang", HANG)
            err, hr = await c.call("deploy_and_run", target=TARGET, binary=hang, timeout_s=1.5)
            assert not err and hr["outcome"] == "timeout" and hr["signal_name"] == "SIGKILL" and hr["stdout"] == "start\n", hr
            assert 1400 <= hr["duration_ms"] < 4000 and hr["group_clean"], hr
            _show("deploy_and_run hang (timeout)", {k: hr[k] for k in ("outcome", "timed_out", "signal_name", "duration_ms", "stdout", "group_clean")})

            # A descendant left in the process group does not outlive the run.
            forker = await _build(c, tmp_path, "forker", FORKER)
            err, fr = await c.call("deploy_and_run", target=TARGET, binary=forker, timeout_s=10)
            child = int(fr["stdout"].strip())
            assert not err and fr["outcome"] == "exited" and fr["group_clean"] is True, fr
            err, pr = await c.call("processes", target=TARGET, pid=child)
            assert not err and pr["returned"] == 0, pr
            _show("deploy_and_run forker (descendant killed)", {"run": {k: fr[k] for k in ("outcome", "exit_code", "group_clean", "duration_ms")},
                                                                 "child_pid": child, "processes(pid=child).returned": pr["returned"]})

            # Missing executable.
            err, mr = await c.call("deploy_and_run", target=TARGET, binary=echo, cwd="/nonexistent-dir")
            assert not err and mr["outcome"] == "exec_failed" and mr["exec_error"] == "No such file or directory", mr

            # Cancelling a call kills the program on the target and frees the target lock.
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(c.session.call_tool("deploy_and_run", {"target": TARGET, "binary": hang, "timeout_s": 120}), 3)
                raise AssertionError("call was not cancelled")
            except asyncio.TimeoutError:
                pass
            gone = False
            for _ in range(20):
                err, pr = await c.call("processes", target=TARGET, name="hang")
                if not err and pr["returned"] == 0:
                    gone = True
                    break
                await asyncio.sleep(0.5)
            assert gone, pr
            err, after = await c.call("deploy_and_run", target=TARGET, binary=echo, args=["after-cancel"], timeout_s=10)
            assert not err and after["stdout"] == "[after-cancel]\n"
            print(f"\n--- cancellation: hang run cancelled after 3 s, no 'hang' process left after {time.monotonic() - t0 - 3:.1f} s, next run ok")

    run_async(go())
    print("\n--- MCP call log\n" + "\n".join(log))


def test_threads_crash(tmp_path: Path):
    async def go():
        async with mcp_client({}) as c:
            exe = await _build(c, tmp_path, "threads", THREADS)
            err, r = await c.call("deploy_and_run", target=TARGET, binary=exe, timeout_s=20)
            assert not err and r["outcome"] == "crashed", r
            err, cr = await c.call("get_crash", target=TARGET, run_id=r["run_id"])
            assert not err, cr
            assert cr["faulting_frame"]["function"] == "crasher" and cr["fault_in_user_code"], cr["faulting_frame"]
            with_frames = [t for t in cr["threads"] if t.get("frames")]
            funcs = {f["function"] for t in with_frames for f in t["frames"]}
            assert len(with_frames) >= 3 and {"main", "waiter", "crasher"} <= funcs, cr["threads"]
            faulting = [t for t in cr["threads"] if t["faulting"]]
            assert len(faulting) == 1 and faulting[0]["frames"][0]["function"] == "crasher", faulting
            _show("get_crash threads", {"summary": cr["summary"], "faulting_thread_id": cr["faulting_thread_id"],
                                        "threads": [{"id": t["id"], "faulting": t["faulting"], "functions": [f["function"] for f in t.get("frames", [])][:5]}
                                                    for t in cr["threads"]]})
    run_async(go())


def test_background_runs_and_inspection(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c:
            ticker = await _build(c, tmp_path, "ticker", TICKER)
            err, rs = await c.call("run_start", target=TARGET, binary=ticker, timeout_s=60)
            assert not err and rs["state"] == "running" and rs["pid"], rs
            rid = rs["run_id"]
            await asyncio.sleep(1.0)
            err, p1 = await c.call("run_poll", target=TARGET, run_id=rid, wait_s=2)
            assert not err and p1["state"] == "running" and p1["stdout"].startswith("tick 0\n"), p1
            # Inspection works while the run is active.
            err, pr = await c.call("processes", target=TARGET, name="ticker", include_threads=True)
            assert not err and pr["returned"] == 1 and pr["processes"][0]["pid"] == rs["pid"], pr
            err, lg = await c.call("logs", target=TARGET, limit=5)
            assert not err, lg
            await asyncio.sleep(0.6)
            err, p2 = await c.call("run_poll", target=TARGET, run_id=rid, stdout_offset=p1["stdout_next"])
            assert not err and p2["stdout_offset"] == p1["stdout_next"] and "tick 0\n" not in p2["stdout"] and p2["stdout"], p2
            err, sp = await c.call("run_stop", target=TARGET, run_id=rid)
            assert not err and sp["was_running"] and sp["acknowledged"] and sp["state"] == "finished" and sp["outcome"] == "stopped", sp
            assert sp["result"]["runner_stopped_by"] == "SIGTERM" and sp["result"]["group_clean"], sp
            err, p3 = await c.call("run_poll", target=TARGET, run_id=rid, stdout_offset=p2["stdout_next"])
            assert not err and p3["state"] == "finished" and p3["outcome"] == "stopped", p3
            err, pr2 = await c.call("processes", target=TARGET, name="ticker")
            assert not err and pr2["returned"] == 0, pr2
            _show("run_start/run_poll/run_stop", {"start": rs, "poll1": {k: p1[k] for k in ("state", "stdout", "stdout_next")},
                                                  "poll2": {k: p2[k] for k in ("state", "stdout_offset", "stdout_next")},
                                                  "processes_during_run": pr["processes"][0], "stop": sp})

            # A background run that crashes leaves its core for get_crash.
            err, b = await c.call("build", path=str(DEMO), output=str(tmp_path / "crash_demo"))
            err, rs2 = await c.call("run_start", target=TARGET, binary=b["artifacts"][0]["path"], timeout_s=30)
            assert not err, rs2
            err, p = await c.call("run_poll", target=TARGET, run_id=rs2["run_id"], wait_s=10)
            for _ in range(10):
                if p["state"] != "running":
                    break
                err, p = await c.call("run_poll", target=TARGET, run_id=rs2["run_id"], wait_s=5)
            assert p["state"] == "finished" and p["outcome"] == "crashed" and p["result"]["core_path"], p
            err, cr = await c.call("get_crash", target=TARGET, run_id=rs2["run_id"])
            assert not err and cr["faulting_frame"]["function"] == "read_calibration" and cr["provenance"]["verified"], cr

            # Another server's (or a made-up) run_id is not accessible.
            err, bad = await c.call("run_stop", target=TARGET, run_id="r-0123456789ab")
            assert err and bad["error"]["code"] == "invalid_argument"

    run_async(go())
    print("\n--- MCP call log\n" + "\n".join(log))


def test_file_transfer(tmp_path: Path):
    async def go():
        async with mcp_client({}) as c:
            data = os.urandom(200_000)
            src = tmp_path / "blob.bin"
            src.write_bytes(data)
            remote = f"/tmp/qnx-mcp-test/{os.getpid()}-{int(time.time())}/blob.bin"
            err, put = await c.call("file_put", target=TARGET, local_path=str(src), remote_path=remote)
            assert not err and put["sha256"] == hashlib.sha256(data).hexdigest() and put["size"] == len(data) and put["mode"] == "0644", put
            err, again = await c.call("file_put", target=TARGET, local_path=str(src), remote_path=remote)
            assert err and again["error"]["code"] == "file_exists", again
            dest = tmp_path / "back.bin"
            err, got = await c.call("file_get", target=TARGET, remote_path=remote, local_path=str(dest))
            assert not err and dest.read_bytes() == data and got["sha256"] == put["sha256"] and "text" not in got, got
            err, nolink = await c.call("file_get", target=TARGET, remote_path=remote, local_path=str(dest))
            assert err and nolink["error"]["code"] == "file_exists"
            (tmp_path / "note.txt").write_text("hello from the host\n")
            err, put2 = await c.call("file_put", target=TARGET, local_path=str(tmp_path / "note.txt"), remote_path=remote + ".txt", mode="0600")
            assert not err and put2["mode"] == "0600", put2
            err, txt = await c.call("file_get", target=TARGET, remote_path=remote + ".txt")
            assert not err and txt["text"] == "hello from the host\n" and txt["mode"] == "0600", txt
            err, missing = await c.call("file_get", target=TARGET, remote_path="/tmp/qnx-mcp-test/does-not-exist")
            assert err and missing["error"]["code"] == "file_not_found", missing
            _show("file_put/file_get", {"put": put, "get": {k: got[k] for k in ("remote_path", "local_path", "size", "sha256")}, "get_text": txt})
    run_async(go())


def test_processes_and_logs():
    async def go():
        async with mcp_client({}) as c:
            err, pr = await c.call("processes", target=TARGET, name="dumper", include_threads=True)
            assert not err and pr["returned"] >= 1 and "/data/var/dumper" in pr["processes"][0]["args"], pr
            assert pr["total_processes"] > 10
            _show("processes name=dumper", pr)
            err, lg = await c.call("logs", target=TARGET, process="sshd", since_s=3600, limit=3)
            assert not err and lg["returned"] >= 1 and all("sshd" in e["process"] for e in lg["entries"]), lg
            _show("logs process=sshd since_s=3600 limit=3", lg)
    run_async(go())


NOISE = r'''#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(void)
{
    static char buf[1 << 20];
    memset(buf, 1, sizeof buf);               /* control characters: 6 bytes each once JSON-escaped */
    fwrite(buf, 1, sizeof buf, stdout);
    fwrite(buf, 1, sizeof buf, stderr);
    fflush(NULL);
    sleep(30);
    return 0;
}
'''


def test_poll_budget_for_escaped_output(tmp_path: Path):
    """Finding I: two streams of control characters expand 6x each in JSON."""
    async def go():
        async with mcp_client({}) as c:
            exe = await _build(c, tmp_path, "noise", NOISE)
            err, rs = await c.call("run_start", target=TARGET, binary=exe, timeout_s=60, max_output_kb=1024)
            assert not err, rs
            for _ in range(20):
                err, p = await c.call("run_poll", target=TARGET, run_id=rs["run_id"], max_kb=1024, wait_s=1)
                assert not err, p
                if p["stdout_size"] == 1 << 20 and p["stderr_size"] == 1 << 20:
                    break
            err, p = await c.call("run_poll", target=TARGET, run_id=rs["run_id"], max_kb=1024)
            assert not err and p["stdout_next"] == 1 << 20 and p["stderr_next"] == 1 << 20 and p["stdout"] == "\x01" * (1 << 20), {k: p[k] for k in ("stdout_next", "stderr_next")}
            err, sp = await c.call("run_stop", target=TARGET, run_id=rs["run_id"])
            assert not err and sp["acknowledged"], sp
            print(f"\n--- run_poll returned 2 x 1 MiB of control characters in one call (stdout_next={p['stdout_next']}, stderr_next={p['stderr_next']})")
    run_async(go())


def test_file_boundaries(tmp_path: Path):
    """Findings B, C and H: normalised destinations, no directory or symlinked-parent destinations,
    no alias of qnx-mcp's own directory, downloads that never chmod the user's directory, and an
    exact copy of a file that grows during the transfer."""
    cfg = Config.from_env()
    base = f"/tmp/qnx-mcp-test/b{os.getpid()}"
    (tmp_path / "f.txt").write_text("x\n")

    async def go():
        async with mcp_client({}) as c:
            err, ok = await c.call("file_put", target=TARGET, local_path=str(tmp_path / "f.txt"), remote_path=f"{base}/dir/f.txt")
            assert not err, ok
            for remote, code in ((f"{base}/dir", "path_not_allowed"), ("/data/var/tmp/qnx-mcp/bin/evil", "path_not_allowed"),
                                 ("/tmp//qnx-mcp/bin/evil", "invalid_argument"), (f"{base}/lnk/f.txt", "path_not_allowed")):
                if remote.endswith("/lnk/f.txt"):
                    Target(cfg, TARGET).ssh(["ln", "-s", "/data/var", f"{base}/lnk"], timeout=15)
                err, bad = await c.call("file_put", target=TARGET, local_path=str(tmp_path / "f.txt"), remote_path=remote, overwrite=True)
                assert err and bad["error"]["code"] == code, (remote, bad)
            err, nolink = await c.call("file_get", target=TARGET, remote_path=f"{base}/lnk/missing")
            assert err, nolink
            shared = tmp_path / "shared"
            shared.mkdir()
            os.chmod(shared, 0o755)
            err, got = await c.call("file_get", target=TARGET, remote_path=f"{base}/dir/f.txt", local_path=str(shared / "f.txt"))
            assert not err and (shared / "f.txt").read_text() == "x\n" and (shared.stat().st_mode & 0o777) == 0o755, got
            # A file that keeps growing: exactly the bytes that were hashed are copied.
            ticker = await _build(c, tmp_path, "ticker", TICKER)
            err, rs = await c.call("run_start", target=TARGET, binary=ticker, timeout_s=30)
            await asyncio.sleep(1.0)
            err, grow = await c.call("file_get", target=TARGET, remote_path=f"/tmp/qnx-mcp/runs/{rs['run_id']}/stdout")
            assert not err and grow["size"] > 0 and Path(grow["local_path"]).stat().st_size == grow["size"], grow
            await c.call("run_stop", target=TARGET, run_id=rs["run_id"])
            print(f"\n--- file_get of a growing file copied exactly the {grow['size']} bytes that were hashed")
    run_async(go())


def test_pruning_and_failed_starts(tmp_path: Path):
    """Findings D and E, in process: a run_start whose acknowledgement is lost stops its daemon, and
    pruning never removes the directory of a live background run or of a one-shot run in progress."""
    cfg = Config.from_env()

    async def build():
        async with mcp_client({}) as c:
            return await _build(c, tmp_path, "ticker", TICKER), await _build(c, tmp_path, "hang", HANG)
    ticker, hang = run_async(build())

    real = ops._parse_runner

    def lose_ack(text, what):
        if what == "run_start":
            raise QnxMcpError("runner_failed", "simulated lost acknowledgement")
        return real(text, what)

    ops._parse_runner = lose_ack
    try:
        with pytest.raises(QnxMcpError):
            ops.run_start(cfg, TARGET, ticker, timeout_s=60)
    finally:
        ops._parse_runner = real
    t = Target(cfg, TARGET)
    failed = [r for r in ops.all_records(t) if r.get("error") == "runner_failed" and r["binary"] == ticker]
    assert failed and failed[-1]["state"] == "failed" and failed[-1]["cleanup"] == "stopped", failed[-1:]
    assert ops.processes(cfg, TARGET, name="ticker")["returned"] == 0

    bg = ops.run_start(cfg, TARGET, ticker, timeout_s=60)
    box: dict = {}
    import threading
    th = threading.Thread(target=lambda: box.update(r=ops.deploy_and_run(cfg, TARGET, hang, timeout_s=6)))
    th.start()
    time.sleep(2.0)
    ip = t.require_ip()
    runner = ops.prepare(t, ip, [], [])
    pr = ops._parse_runner(t.ssh([runner, "-G", "/tmp/qnx-mcp/runs", "-k", "0", "-a", "0"], timeout=30, ip=ip).out, "prune")
    th.join(30)
    assert box["r"]["outcome"] == "timeout" and box["r"]["warnings"] == [], box
    p = ops.run_poll(cfg, TARGET, bg["run_id"])
    assert p["state"] == "running" and p["stdout"].startswith("tick 0"), p
    ops.run_stop(cfg, TARGET, bg["run_id"])
    assert pr["skipped_live"] >= 2, pr
    print(f"\n--- lost run_start acknowledgement: cleanup={failed[-1]['cleanup']}; prune with keep=0 age=0 during two live runs: {pr}")
