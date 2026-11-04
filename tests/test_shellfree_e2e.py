# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""The v0.2 tools an agent needs when it has no shell on the target, against a real QNX 8.0 target:
file_list, deploy_and_run/run_start with remote_binary, and process_stop, each with its refusals.

The test itself uses ssh (Target.ssh) only to set up what an agent could not: symlinks and processes
that qnx-mcp did not start. Set QNX_MCP_E2E_TARGET=<running target name>.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
from pathlib import Path

import pytest

from helpers import mcp_client, run_async
from qnx_mcp.sdk import runner_binary
from qnx_mcp.target import Target
from qnx_mcp.util import Config, quote_argv

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
DEMO = Path(__file__).resolve().parents[1] / "examples" / "crash_demo"

ECHOARGS = '#include <stdio.h>\nint main(int c, char **v){ for (int i = 1; i < c; i++) printf("[%s]\\n", v[i]); return 7; }\n'
HANG = '#include <unistd.h>\nint main(void){ for (;;) sleep(1); }\n'
STUBBORN = '#include <signal.h>\n#include <unistd.h>\nint main(void){ signal(SIGTERM, SIG_IGN); for (;;) sleep(1); }\n'
SHAPESHIFTER = r'''#include <signal.h>
#include <unistd.h>
static void on_term(int s) { (void)s; char *argv[] = {"sleep", "30", NULL}; execv("/system/bin/sleep", argv); }
int main(void) { signal(SIGTERM, on_term); for (;;) sleep(1); }
'''
TICKER = '#include <stdio.h>\n#include <unistd.h>\nint main(void){ for (int i = 0;; i++) { printf("tick %d\\n", i); fflush(stdout); usleep(200000); } }\n'


def _show(title: str, data: object) -> None:
    print(f"\n--- {title}\n{json.dumps(data, indent=1)[:2500]}")


def _target() -> Target:
    return Target(Config.from_env(), TARGET)


def _detached(argv: list[str]) -> None:
    """Start a process on the target outside qnx-mcp (test setup only)."""
    _target().ssh_script(f"({quote_argv(argv)} >/dev/null 2>&1 &)", timeout=30, what="test setup")


async def _build(c, tmp_path: Path, name: str, src: str) -> str:
    (tmp_path / f"{name}.c").write_text(src)
    err, b = await c.call("build", path=str(tmp_path / f"{name}.c"))
    assert not err and b["artifacts"], b
    return b["artifacts"][0]["path"]


async def _install(c, local: str, remote: str, mode: str = "0755") -> None:
    err, r = await c.call("file_put", target=TARGET, local_path=local, remote_path=remote, mode=mode, overwrite=True)
    assert not err, r


async def _pid_of(c, path_fragment: str) -> int:
    for _ in range(20):
        err, ps = await c.call("processes", target=TARGET, name=path_fragment)
        assert not err, ps
        if ps["processes"]:
            return ps["processes"][0]["pid"]
        await asyncio.sleep(0.25)
    raise AssertionError(f"no process matching {path_fragment}")


def test_file_list(tmp_path: Path):
    base = f"/tmp/qnx-mcp-test/l{os.getpid()}"
    (tmp_path / "a.txt").write_text("hello\n")

    async def go():
        async with mcp_client({}) as c:
            await _install(c, str(tmp_path / "a.txt"), f"{base}/a.txt", "0644")
            await _install(c, str(tmp_path / "a.txt"), f"{base}/sub/b.txt", "0600")
            _target().ssh_script(f"ln -s /proc/boot {quote_argv([base + '/boot'])} && ln -s a.txt {quote_argv([base + '/alink'])}",
                                 timeout=15, what="test setup")
            err, ls = await c.call("file_list", target=TARGET, path=base)
            assert not err and ls["total_entries"] == 4 and ls["returned"] == 4 and ls["truncated"] is False, ls
            ents = {e["name"]: e for e in ls["entries"]}
            assert [e["name"] for e in ls["entries"]] == ["a.txt", "alink", "boot", "sub"], ls
            assert ents["a.txt"]["type"] == "file" and ents["a.txt"]["size"] == 6 and ents["a.txt"]["mode"] == "0644" and ents["a.txt"]["mtime_iso"]
            assert ents["sub"]["type"] == "dir"
            # links are described, never followed: the size is the link's own and the target is reported
            assert ents["boot"]["type"] == "symlink" and ents["boot"]["link_target"] == "/proc/boot" and ents["boot"]["size"] == len("/proc/boot")
            assert ents["alink"]["type"] == "symlink" and ents["alink"]["link_target"] == "a.txt"
            assert ls["real_path"].endswith(base[len("/tmp"):]) and ls["real_path_verified"] is True and ls["complete"] is True, ls
            _show("file_list", ls)

            err, part = await c.call("file_list", target=TARGET, path=base, max_entries=2)
            assert not err and [e["name"] for e in part["entries"]] == ["a.txt", "alink"] and part["truncated"] and part["total_entries"] == 4, part
            _show("file_list max_entries=2", {k: part[k] for k in ("total_entries", "returned", "truncated", "note")})
            err, tmp = await c.call("file_list", target=TARGET, path="/tmp")
            assert not err and tmp["real_path"] != "/tmp" and any(e["name"] == "qnx-mcp" for e in tmp["entries"]), tmp
            err, root = await c.call("file_list", target=TARGET, path="/")
            assert not err and {"proc", "tmp", "data"} <= {e["name"] for e in root["entries"]}, root
            err, via = await c.call("file_list", target=TARGET, path=f"{base}/boot")
            assert not err and via["real_path"] == "/proc/boot" and any(e["name"].startswith("procnto") for e in via["entries"]), via
            print(f"\n--- file_list through a link: path {via['path']} real_path {via['real_path']} ({via['total_entries']} entries)")

            for path, code in ((f"{base}/../l", "invalid_argument"), (f"{base}/./sub", "invalid_argument"), (f"{base}/a.txt", "invalid_argument"),
                               (f"{base}/missing", "file_not_found"), (f"{base}/", "invalid_argument"), ("tmp", "invalid_argument")):
                err, bad = await c.call("file_list", target=TARGET, path=path)
                assert err and bad["error"]["code"] == code, (path, bad)
                print(f"refused file_list({path!r}): {bad['error']['code']}: {bad['error']['message']}")
    run_async(go())


def test_remote_binary_runs(tmp_path: Path):
    base = f"/tmp/qnx-mcp-test/r{os.getpid()}"

    async def go():
        async with mcp_client({}) as c:
            exe = tmp_path / "crash_demo"
            err, b = await c.call("build", path=str(DEMO), output=str(exe))
            assert not err, b
            echo = await _build(c, tmp_path, "echoargs", ECHOARGS)
            ticker = await _build(c, tmp_path, "ticker", TICKER)
            for local, name, mode in ((echo, "echoargs", "0755"), (str(exe), "crash_demo", "0755"), (ticker, "ticker", "0755"), (echo, "noexec", "0644")):
                await _install(c, local, f"{base}/bin/{name}", mode)

            err, r = await c.call("deploy_and_run", target=TARGET, remote_binary=f"{base}/bin/echoargs", args=["a b", "$(id)"], timeout_s=20)
            assert not err and r["outcome"] == "exited" and r["exit_code"] == 7 and r["stdout"] == "[a b]\n[$(id)]\n", r
            assert r["remote_binary"] is True and r["binary"] is None and r["remote_path"] == f"{base}/bin/echoargs", r
            assert r["binary_sha256"] == hashlib.sha256(Path(echo).read_bytes()).hexdigest(), r
            _show("deploy_and_run(remote_binary=...)", {k: r[k] for k in ("run_id", "outcome", "exit_code", "stdout", "remote_binary", "binary", "remote_path", "binary_sha256", "duration_ms")})

            err, cr = await c.call("deploy_and_run", target=TARGET, remote_binary=f"{base}/bin/crash_demo", timeout_s=20)
            assert not err and cr["outcome"] == "crashed" and cr["dumper"] == "notified" and cr["core_path"].startswith(f"/tmp/qnx-mcp/runs/{cr['run_id']}/"), cr
            assert "binary=<host path of the unstripped build" in cr["next_step"], cr
            _show("deploy_and_run(remote_binary=crash_demo)", {k: cr[k] for k in ("run_id", "outcome", "signal_name", "core_path", "next_step")})
            err, need = await c.call("get_crash", target=TARGET, run_id=cr["run_id"])
            assert err and need["error"]["code"] == "invalid_argument" and "binary=" in need["error"]["hint"], need
            _show("get_crash without binary", need)
            err, g = await c.call("get_crash", target=TARGET, run_id=cr["run_id"], binary=str(exe))
            assert not err and g["faulting_frame"]["function"] == "read_calibration" and g["fault_address"] == "0x0", g
            pv = g["provenance"]
            assert pv["source"] == "remote_run" and pv["verified"] and pv["executable_match"] and pv["pid_match"], pv
            assert pv["build_id_match"] is True and pv["core_executable"] == f"{base}/bin/crash_demo" and "build ID" in pv["evidence"], pv
            _show("get_crash(run_id, binary)", {"summary": g["summary"], "provenance": pv})
            # Without a build ID nothing ties the host binary to the code that ran: a backtrace, but never verified.
            err, nb = await c.call("build", path=str(DEMO), output=str(tmp_path / "nobid"), ldflags=["-Wl,--build-id=none"])
            assert not err and nb["artifacts"][0]["build_id"] is None, nb
            await _install(c, nb["artifacts"][0]["path"], f"{base}/bin/nobid")
            err, cn = await c.call("deploy_and_run", target=TARGET, remote_binary=f"{base}/bin/nobid", timeout_s=20)
            assert not err and cn["outcome"] == "crashed", cn
            err, gn = await c.call("get_crash", target=TARGET, run_id=cn["run_id"], binary=nb["artifacts"][0]["path"])
            pn = gn.get("provenance", {})
            assert not err and pn["verified"] is False and pn["build_id_match"] is None and pn["pid_match"] and pn["executable_match"], gn
            assert pn["evidence"].startswith("pid and path match; build ID not available"), pn
            _show("get_crash(run_id, binary) without a build ID", {"summary": gn["summary"][:90], "provenance": pn})
            err, b2 = await c.call("build", path=str(DEMO), output=str(tmp_path / "rebuilt"), cflags=["-g", "-O0", "-DREBUILT=1", "-fno-inline"])
            assert not err, b2
            err, mm = await c.call("get_crash", target=TARGET, run_id=cr["run_id"], binary=b2["artifacts"][0]["path"])
            assert err and mm["error"]["code"] == "core_mismatch", mm
            print(f"\nget_crash with a rebuilt binary: {mm['error']['code']}: {mm['error']['message']}")

            err, st = await c.call("run_start", target=TARGET, remote_binary=f"{base}/bin/ticker", timeout_s=30)
            assert not err and st["remote_binary"] is True, st
            await asyncio.sleep(0.8)
            err, p = await c.call("run_poll", target=TARGET, run_id=st["run_id"])
            assert not err and p["state"] == "running" and "tick 0" in p["stdout"], p
            err, sp = await c.call("run_stop", target=TARGET, run_id=st["run_id"])
            assert not err and sp["acknowledged"] and sp["outcome"] == "stopped", sp

            err, ls = await c.call("file_list", target=TARGET, path="/tmp/qnx-mcp/bin")
            runner = next(f"/tmp/qnx-mcp/bin/{e['name']}" for e in ls["entries"] if e["name"].startswith("qnx-mcp-run-"))
            _target().ssh(["ln", "-s", runner, f"{base}/bin/runner-link"], timeout=15)
            _target().ssh(["ln", "-s", "/proc/boot/ksh", f"{base}/bin/innocent"], timeout=15)
            _target().ssh(["ln", "-s", "/system/bin/slay", f"{base}/bin/helper"], timeout=15)
            for remote, code in ((runner, "path_not_allowed"), (f"{base}/bin/runner-link", "path_not_allowed"), (f"{base}/bin/noexec", "invalid_argument"),
                                 (f"{base}/bin/missing", "file_not_found"), (f"{base}/../r/bin/echoargs", "invalid_argument"), (f"{base}/bin", "invalid_argument"),
                                 ("/proc/boot/ksh", "path_not_allowed"), ("/system/bin/slay", "path_not_allowed"), (f"{base}/bin/innocent", "path_not_allowed"),
                                 (f"{base}/bin/helper", "path_not_allowed")):
                for tool in ("deploy_and_run", "run_start"):
                    err, bad = await c.call(tool, target=TARGET, remote_binary=remote)
                    assert err and bad["error"]["code"] == code, (tool, remote, bad)
                print(f"refused remote_binary={remote!r}: {bad['error']['code']}: {bad['error']['message']}")
    run_async(go())


def test_process_stop(tmp_path: Path):
    base = f"/tmp/qnx-mcp-test/p{os.getpid()}"

    async def go():
        async with mcp_client({}) as c:
            await _install(c, await _build(c, tmp_path, "hang", HANG), f"{base}/hang")
            await _install(c, await _build(c, tmp_path, "stubborn", STUBBORN), f"{base}/stubborn")
            _target().ssh(["ln", "-s", "/system/bin/sleep", f"{base}/sleeplink"], timeout=15)
            for argv in ([f"{base}/hang"], [f"{base}/stubborn"], [f"{base}/sleeplink", "120"], ["/system/bin/sleep", "121"]):
                _detached(argv)

            pid = await _pid_of(c, f"{base[1:]}/hang")
            err, s = await c.call("process_stop", target=TARGET, pid=pid)
            assert not err and s["signal"] == "SIGTERM" and s["signal_sent"] and s["exited"] and not s["still_running"] and not s["sigkill_sent"], s
            assert s["start_ns"] > 1_700_000_000 * 10**9, s
            assert s["executable"] == f"{base}/hang" and s["real_path"].endswith(f"{base[len('/tmp'):]}/hang"), s
            err, gone = await c.call("processes", target=TARGET, pid=pid)
            assert not err and gone["returned"] == 0, gone
            _show("process_stop", s)

            # Services started with a relative path, as the field services in the evaluation are (cd dir && ./prog &).
            _target().ssh_script(f"cd {quote_argv([base])} && (./hang >/dev/null 2>&1 &); cd /tmp && (qnx-mcp-test/p{os.getpid()}/hang >/dev/null 2>&1 &); "
                                 "cd /system/bin && (./sleep 122 >/dev/null 2>&1 &)", timeout=30, what="test setup")
            await asyncio.sleep(0.5)
            err, ps = await c.call("processes", target=TARGET, name="hang")
            rel = [p for p in ps["processes"] if p["path"] in ("./hang", f"qnx-mcp-test/p{os.getpid()}/hang")]
            assert len(rel) == 2, ps
            for p in rel:
                err, s = await c.call("process_stop", target=TARGET, pid=p["pid"])
                assert not err and s["exited"] and posixpath.normpath(s["executable"]).endswith(f"{base[len('/tmp'):]}/hang"), s
                print(f"\nprocess_stop of a service started as {p['path']}: executable {s['executable']}, real_path {s['real_path']}, exited {s['exited']}")
            err, ps = await c.call("processes", target=TARGET, name="sleep")
            dotsleep = next(p["pid"] for p in ps["processes"] if p["path"] == "./sleep")
            err, bad = await c.call("process_stop", target=TARGET, pid=dotsleep)
            assert err and bad["error"]["code"] == "process_not_allowed" and "/system" in bad["error"]["message"], bad
            print(f"refused process_stop of ./sleep started in /system/bin: {bad['error']['message']}")
            _target().ssh_script(f"kill {dotsleep}", timeout=15, what="test cleanup", check=False)

            pid = await _pid_of(c, f"{base[1:]}/stubborn")
            err, s1 = await c.call("process_stop", target=TARGET, pid=pid, grace_s=1)
            assert not err and s1["signal_sent"] and not s1["exited"] and s1["still_running"] and "kill_after_grace" in s1["next_step"], s1
            err, s2 = await c.call("process_stop", target=TARGET, pid=pid, grace_s=1, kill_after_grace=True)
            assert not err and s2["sigkill_sent"] and s2["exited"], s2
            _show("process_stop of a program that ignores SIGTERM, then kill_after_grace=true", [s1, s2])

            # A program that execs a system program when it gets SIGTERM: SIGKILL is withheld after the grace period.
            await _install(c, await _build(c, tmp_path, "shapeshifter", SHAPESHIFTER), f"{base}/shapeshifter")
            _detached([f"{base}/shapeshifter"])
            pid = await _pid_of(c, f"{base[1:]}/shapeshifter")
            err, sh = await c.call("process_stop", target=TARGET, pid=pid, grace_s=1, kill_after_grace=True)
            assert err and sh["error"]["code"] == "process_not_allowed" and "SIGKILL was not sent" in sh["error"]["message"], sh
            assert sh["error"]["details"]["signal_sent"] is True and sh["error"]["details"]["sigkill_sent"] is False, sh
            err, now = await c.call("processes", target=TARGET, pid=pid)
            assert not err and now["returned"] == 1 and "system/bin/sleep" in now["processes"][0]["path"], now
            print(f"\nprocess_stop of a program that execs /system/bin/sleep on SIGTERM: {sh['error']['message']}")
            # A creation token that does not match the process is refused by the runner before any signal.
            runner = f"/tmp/qnx-mcp/bin/qnx-mcp-run-{runner_binary(Config.from_env(), 'x86_64')[1]}"
            r = _target().ssh([runner, "-Z", str(pid), "-T", "1", "-q", now["processes"][0]["path"], "-s", "SIGKILL", "-w", "0", "-A", "/tmp", "-A", "/system"],
                              timeout=15, check=False)
            assert "changed: its start time differs" in r.out, r.out
            print(f"runner -Z with a wrong start time: {r.out.strip()}")
            _target().ssh_script(f"kill -9 {pid}", timeout=15, what="test cleanup", check=False)

            err, ps = await c.call("processes", target=TARGET, name="slogger2")
            slogger = ps["processes"][0]["pid"]
            link = await _pid_of(c, f"{base[1:]}/sleeplink")
            system = await _pid_of(c, "system/bin/sleep")
            err, rs = await c.call("run_start", target=TARGET, binary=await _build(c, tmp_path, "ticker", TICKER), timeout_s=30)
            assert not err, rs
            for victim, why in ((1, "procnto"), (slogger, "/proc/boot"), (system, "/system"), (link, "resolves to /system/bin/sleep"),
                                (rs["runner_pid"], "runner")):
                err, bad = await c.call("process_stop", target=TARGET, pid=victim)
                assert err and bad["error"]["code"] == "process_not_allowed" and why in bad["error"]["message"] and bad["error"]["hint"], (victim, bad)
                print(f"refused process_stop(pid={victim}): {bad['error']['message']}")
            err, alive = await c.call("processes", target=TARGET, pid=link)
            assert not err and alive["returned"] == 1, alive
            err, none = await c.call("process_stop", target=TARGET, pid=999999999)
            assert err and none["error"]["code"] == "process_not_found", none
            err, sp = await c.call("run_stop", target=TARGET, run_id=rs["run_id"])
            assert not err and sp["acknowledged"], sp
            _target().ssh_script(f"kill {link} {system}", timeout=15, what="test cleanup", check=False)
    run_async(go())
