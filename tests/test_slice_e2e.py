# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Vertical slice through MCP over stdio against a real QNX 8.0 target.

build -> deploy_and_run (crashes) -> get_crash, then the safety checks
(argument passing without a shell, timeout kill, compile diagnostics) and
processes/logs. Set QNX_MCP_E2E_TARGET=<running target name>.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helpers import mcp_client, run_async

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
DEMO = Path(__file__).resolve().parents[1] / "examples" / "crash_demo"


def _crash_line() -> int:
    for n, line in enumerate((DEMO / "crash.c").read_text().splitlines(), 1):
        if "CRASH-LINE" in line:
            return n
    raise AssertionError("marker missing")


def _show(title: str, data: object) -> None:
    print(f"\n--- {title}\n{json.dumps(data, indent=1)[:2500]}")


def test_vertical_slice(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c:
            err, st = await c.call("target_status", name=TARGET)
            assert not err and st["state"] == "running" and st["dumper_running"] is True, st
            _show("target_status", {k: st[k] for k in ("name", "state", "ip", "uname", "dumper_running")})

            err, b = await c.call("build", path=str(DEMO), output=str(tmp_path / "crash_demo"))
            assert not err and b["ok"] and b["error_count"] == 0, b
            art = b["artifacts"][0]
            assert art["arch"] == "x86_64" and art["qnx"] is True and art["debug_info"] is True, art
            _show("build", {"ok": b["ok"], "argv": b["steps"][0]["argv"], "artifact": art})

            err, r = await c.call("deploy_and_run", target=TARGET, binary=art["path"], timeout_s=20)
            assert not err and r["outcome"] == "crashed" and r["signal_name"] == "SIGSEGV", r
            assert r["core_path"] and r["dumper"] == "notified" and "imu: average offset 6" in r["stdout"], r
            _show("deploy_and_run", {k: r[k] for k in ("run_id", "outcome", "signal_name", "exit_code", "stdout", "dumper", "core_path", "duration_ms", "next_step")})

            err, cr = await c.call("get_crash", target=TARGET, run_id=r["run_id"])
            assert not err, cr
            f0, f1, f2 = cr["frames"][:3]
            assert (f0["function"], Path(f0["file"]).name, f0["line"]) == ("read_calibration", "crash.c", _crash_line()), f0
            assert f0["fullname"] == str(DEMO / "crash.c")
            assert (f1["function"], f2["function"]) == ("average_offset", "main")
            assert f1["line"] and f2["line"] and f0["user_code"] and f0["source"]
            assert cr["fault_address"] == "0x0" and cr["signal_name"] == "SIGSEGV"
            assert any(v["name"] == "s" for v in f0["locals"]) and any(v["name"] == "index" for v in f0["locals"])
            _show("get_crash", {"summary": cr["summary"], "fault_address": cr["fault_address"], "faulting_instruction": cr["faulting_instruction"],
                                "frames": [{k: f[k] for k in ("level", "function", "file", "line", "address")} for f in cr["frames"]],
                                "crash_line_source": [s for s in f0["source"] if s["current"]], "locals_frame0": f0["locals"]})

            err, ok_run = await c.call("deploy_and_run", target=TARGET, binary=art["path"], args=["0"], timeout_s=20)
            assert not err and ok_run["outcome"] == "exited" and ok_run["exit_code"] == 0 and ok_run["core_path"] is None, ok_run

            # Arguments arrive verbatim: no remote shell expansion.
            (tmp_path / "echoargs.c").write_text('#include <stdio.h>\nint main(int c, char **v){ for (int i = 1; i < c; i++) printf("[%s]\\n", v[i]); return 7; }\n')
            err, eb = await c.call("build", path=str(tmp_path / "echoargs.c"))
            assert not err, eb
            evil = ["$(touch /tmp/qnx-mcp-pwned)", "a b; touch /tmp/qnx-mcp-pwned", "`id`", "it's", "*"]
            err, er = await c.call("deploy_and_run", target=TARGET, binary=eb["artifacts"][0]["path"], args=evil)
            assert not err and er["exit_code"] == 7 and er["stdout"] == "".join(f"[{a}]\n" for a in evil), er
            _show("deploy_and_run echoargs (injection attempt)", {k: er[k] for k in ("outcome", "exit_code", "stdout")})

            # Timeout kills the program.
            (tmp_path / "hang.c").write_text('#include <unistd.h>\n#include <stdio.h>\nint main(void){ puts("start"); fflush(stdout); for (;;) sleep(1); }\n')
            err, hb = await c.call("build", path=str(tmp_path / "hang.c"))
            err, hr = await c.call("deploy_and_run", target=TARGET, binary=hb["artifacts"][0]["path"], timeout_s=1.5)
            assert not err and hr["outcome"] == "timeout" and hr["signal_name"] == "SIGKILL" and hr["stdout"] == "start\n", hr
            assert 1400 <= hr["duration_ms"] < 4000, hr
            _show("deploy_and_run hang (timeout)", {k: hr[k] for k in ("outcome", "timed_out", "signal_name", "duration_ms", "stdout")})

            # Compiler diagnostics as objects.
            (tmp_path / "bad.c").write_text("int f(int x)\n{\n    int unused;\n    return x + y;\n}\n")
            err, bb = await c.call("build", path=str(tmp_path / "bad.c"))
            assert err and bb["ok"] is False and bb["error"]["code"] == "build_failed", bb
            e0 = next(d for d in bb["diagnostics"] if d["severity"] == "error")
            assert (Path(e0["file"]).name, e0["line"], e0["column"]) == ("bad.c", 4, 16) and "'y' undeclared" in e0["message"], e0
            w0 = next(d for d in bb["diagnostics"] if d["severity"] == "warning")
            assert w0["option"] == "-Wunused-variable" and w0["line"] == 3
            _show("build bad.c (diagnostics)", bb["diagnostics"])

            err, pr = await c.call("processes", target=TARGET, name="dumper", include_threads=True)
            assert not err and pr["returned"] >= 1 and "/data/var/dumper" in pr["processes"][0]["args"], pr
            assert pr["total_processes"] > 10
            _show("processes name=dumper", pr)

            err, lg = await c.call("logs", target=TARGET, process="sshd", since_s=900, limit=3)
            assert not err and lg["returned"] >= 1 and all("sshd" in e["process"] for e in lg["entries"]), lg
            _show("logs process=sshd since_s=900 limit=3", lg)

    run_async(go())
    print("\n--- MCP call log\n" + "\n".join(log))
