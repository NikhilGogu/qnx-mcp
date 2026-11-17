# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Non-root runs on a real QNX 8.0 target: with QNX_MCP_RUN_AS set, the agent's program runs as that user,
cannot do what root can (examples/escape_probe), still crashes with a verified backtrace, and a process it
leaves behind with setsid() can be ended with process_stop.

Set QNX_MCP_E2E_TARGET=<running target name> and QNX_MCP_E2E_RUN_AS=<uid:gid> (numeric, non-root; the user
need not exist in /etc/passwd). A second user, uid+1:gid+1, stands for another local user of the target. Run on a
QNX 8.0 x86_64 QEMU target as 1000:1000; the errno each refused attempt expects is what that target returned.
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
TICKER = ('#include <stdio.h>\n#include <unistd.h>\nint main(void){ FILE *f = fopen("made-by-program.txt", "w"); if (f) { fputs("x\\n", f); fclose(f); } '
          'for (int i = 0;; i++) { printf("tick %d\\n", i); fflush(stdout); usleep(200000); } }\n')
# Inside qnx-mcp's directory a program may create files only in its own work directory.
NO_WRITE = ("create_in_own_run_dir", "create_in_runs", "create_in_qnx_mcp_root", "create_in_apps", "create_in_bin", "create_in_own_app_dir")
NO_LIST = ("list_qnx_mcp_root", "list_runs", "list_apps")
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
                assert got["create_in_own_work"]["succeeded"] is True, got["create_in_own_work"]
                for name in NO_WRITE + NO_LIST:
                    assert got[name]["succeeded"] is False and got[name]["errno_name"] in REFUSED, got[name]
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


def _other_user() -> str:
    uid, gid = _ids()
    return f"{uid + 1}:{gid + 1}"


def test_other_users_cannot_read_a_run_or_its_core(tmp_path: Path):
    """A run as RUN_AS leaves output, a file in its work directory and a core. Another non-root user (uid+1) cannot
    list or read any of it, and the run's own user cannot change the core once the runner has taken it."""
    async def go():
        async with mcp_client({"QNX_MCP_RUN_AS": RUN_AS}) as c:
            err, b = await c.call("build", path=str(PROBE), output=str(tmp_path / "escape"))
            assert not err, b
            (tmp_path / "ticker.c").write_text(TICKER)
            err, tb = await c.call("build", path=str(tmp_path / "ticker.c"))
            assert not err, tb
            err, cb = await c.call("build", path=str(DEMO), output=str(tmp_path / "crash_demo"))
            assert not err, cb
            err, k = await c.call("deploy_and_run", target=TARGET, binary=str(tmp_path / "crash_demo"), timeout_s=30)
            assert not err and k["outcome"] == "crashed" and k["core_path"].startswith(f"/tmp/qnx-mcp/runs/{k['run_id']}/"), k
            err, ls = await c.call("file_list", target=TARGET, path=f"/tmp/qnx-mcp/runs/{k['run_id']}")
            core = next(e for e in ls["entries"] if e["name"] == k["core_path"].rsplit("/", 1)[1])
            print("\n--- core as listed:", json.dumps(core))
            assert core["mode"] == "0600", core
            err, own = await c.call("deploy_and_run", target=TARGET, binary=str(tmp_path / "escape"), args=["--core", k["core_path"]], timeout_s=30)
            got = _attempts(own["stdout"])
            print("--- the run's own user against the core:", json.dumps([got["open_core_read"], got["open_core_write"]]))
            for name in ("open_core_read", "open_core_write"):
                assert got[name]["succeeded"] is False and got[name]["errno_name"] in REFUSED, got[name]
            err, other = await c.call("run_start", target=TARGET, binary=tb["artifacts"][0]["path"], timeout_s=60)
            assert not err, other
        other_dir = f"/tmp/qnx-mcp/runs/{other['run_id']}"
        try:
            async with mcp_client({"QNX_MCP_RUN_AS": _other_user()}) as c:
                await asyncio.sleep(1)
                err, r = await c.call("deploy_and_run", target=TARGET, binary=str(tmp_path / "escape"),
                                      args=["--other-run", other_dir, "--core", k["core_path"]], timeout_s=30)
                assert not err and r["outcome"] == "exited", r
                got = _attempts(r["stdout"])
                print("--- escape_probe as", _other_user(), "against a run of", RUN_AS)
                for a in got.values():
                    print(json.dumps(a))
                for name in ("list_other_run", "read_other_run_output", "list_other_run_work", "read_other_run_work_file",
                             "open_core_read", "open_core_write") + NO_WRITE + NO_LIST:
                    assert got[name]["succeeded"] is False and got[name]["errno_name"] in REFUSED, got[name]
                assert got["create_in_own_work"]["succeeded"] is True, got["create_in_own_work"]
        finally:
            async with mcp_client({"QNX_MCP_RUN_AS": RUN_AS}) as c:
                err, p = await c.call("run_stop", target=TARGET, run_id=other["run_id"])
                assert not err, p
    run_async(go())
