# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Non-root runs on a real QNX 8.0 target: with QNX_MCP_RUN_AS set, the agent's program runs as that user,
cannot do what root can (examples/escape_probe), still crashes with a verified backtrace, and a process it
leaves behind with setsid() can be ended with process_stop.

NOT YET RUN ON A TARGET. Set QNX_MCP_E2E_TARGET=<running target name> and QNX_MCP_E2E_RUN_AS=<uid:gid>
(numeric, non-root; the user need not exist in /etc/passwd). The expected errno for each refused attempt is
EPERM or EACCES (EROFS for read-only file systems); the test prints what the target returned, so the first run
records QNX's real answers.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from helpers import mcp_client, run_async

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
RUN_AS = os.environ.get("QNX_MCP_E2E_RUN_AS")
pytestmark = [pytest.mark.target,
              pytest.mark.skipif(not (TARGET and RUN_AS), reason="set QNX_MCP_E2E_TARGET and QNX_MCP_E2E_RUN_AS=uid:gid")]
ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "examples" / "escape_probe" / "escape.c"
DEMO = ROOT / "examples" / "crash_demo"
TICKER = '#include <stdio.h>\n#include <unistd.h>\nint main(void){ for (int i = 0;; i++) { printf("tick %d\\n", i); fflush(stdout); usleep(200000); } }\n'
REFUSED = {"EPERM", "EACCES"}
REFUSED_OR_READ_ONLY = REFUSED | {"EROFS"}
# /proc/boot is the boot image; QNX 8.0 answers a create there with ENOENT (seen on a QEMU target as uid 1000).
REFUSED_IN_PROC_BOOT = REFUSED_OR_READ_ONLY | {"ENOENT"}


def _attempts(stdout: str) -> dict[str, dict]:
    return {a["attempt"]: a for a in (json.loads(line) for line in stdout.splitlines() if line.startswith("{"))}


def _ids() -> tuple[int, int]:
    uid, gid = RUN_AS.split(":")
    return int(uid), int(gid)


def test_program_runs_as_run_as_and_cannot_escalate(tmp_path: Path):
    uid, gid = _ids()

    async def go():
        async with mcp_client({"QNX_MCP_RUN_AS": RUN_AS}) as c:
            err, env = await c.call("environment_check")
            assert env["privileges"]["program_identity"] == "run_as" and env["privileges"]["run_as"] == {"uid": uid, "gid": gid}, env
            err, b = await c.call("build", path=str(PROBE), output=str(tmp_path / "escape"))
            assert not err, b
            (tmp_path / "ticker.c").write_text(TICKER)
            err, tb = await c.call("build", path=str(tmp_path / "ticker.c"))
            assert not err, tb

            # Another run's directory, with output files the runner wrote: the probe must not reach them.
            err, other = await c.call("run_start", target=TARGET, binary=tb["artifacts"][0]["path"], timeout_s=60)
            assert not err and other["run_as"] == {"uid": uid, "gid": gid}, other
            other_dir = f"/tmp/qnx-mcp/runs/{other['run_id']}"
            try:
                err, r = await c.call("deploy_and_run", target=TARGET, binary=str(tmp_path / "escape"), args=["--other-run", other_dir], timeout_s=30)
                assert not err and r["outcome"] == "exited" and r["exit_code"] == 0 and r["run_as"] == {"uid": uid, "gid": gid}, r
                got = _attempts(r["stdout"])
                print("\n--- escape_probe as", RUN_AS)
                for a in got.values():
                    print(json.dumps(a))
                ident = got["identity"]
                assert (ident["uid"], ident["euid"], ident["gid"], ident["egid"]) == (uid, uid, gid, gid), ident
                for name in ("signal_procnto", "signal_runner", "open_procnto_ctl_rw", "setuid_0",
                             "threadctl_io", "mmap_device_memory", "list_other_run", "read_other_run_output"):
                    assert got[name]["succeeded"] is False and got[name]["errno_name"] in REFUSED, got[name]
                for name in ("create_in_system_bin", "create_in_etc"):
                    assert got[name]["succeeded"] is False and got[name]["errno_name"] in REFUSED_OR_READ_ONLY, got[name]
                assert got["create_in_proc_boot"]["succeeded"] is False and got["create_in_proc_boot"]["errno_name"] in REFUSED_IN_PROC_BOOT
                # The run's own output still reaches the agent through the runner.
                err, p = await c.call("run_poll", target=TARGET, run_id=other["run_id"])
                assert not err and "tick" in p["stdout"], p
            finally:
                await c.call("run_stop", target=TARGET, run_id=other["run_id"])
    run_async(go())


def test_crash_is_still_verified_as_run_as(tmp_path: Path):
    async def go():
        async with mcp_client({"QNX_MCP_RUN_AS": RUN_AS}) as c:
            exe = tmp_path / "crash_demo"
            err, b = await c.call("build", path=str(DEMO), output=str(exe))
            assert not err, b
            err, r = await c.call("deploy_and_run", target=TARGET, binary=str(exe), timeout_s=30)
            print("\n--- crash_demo as", RUN_AS, json.dumps({k: r.get(k) for k in ("outcome", "dumper", "core_path", "exec_error", "run_as")}))
            assert not err and r["outcome"] == "crashed" and r["dumper"] == "notified" and r["core_path"], r
            err, cr = await c.call("get_crash", target=TARGET, run_id=r["run_id"])
            assert not err and cr["provenance"]["verified"] is True and cr["faulting_frame"]["function"] == "read_calibration", cr
    run_async(go())


def test_setsid_escape_can_be_stopped(tmp_path: Path):
    async def go():
        async with mcp_client({"QNX_MCP_RUN_AS": RUN_AS}) as c:
            err, b = await c.call("build", path=str(PROBE), output=str(tmp_path / "escape"))
            assert not err, b
            err, r = await c.call("deploy_and_run", target=TARGET, binary=str(tmp_path / "escape"), args=["--escape"], timeout_s=30)
            assert not err and r["outcome"] == "exited", r
            child = _attempts(r["stdout"])["escape"]["child"]
            await asyncio.sleep(1)
            err, ps = await c.call("processes", target=TARGET, pid=child)
            assert not err and ps["processes"], ("the escaped child should outlive the run", ps)
            err, st = await c.call("process_stop", target=TARGET, pid=child, kill_after_grace=True)
            assert not err and st["signal_sent"] and not st["still_running"] and st["uid"] == _ids()[0], st
    run_async(go())
