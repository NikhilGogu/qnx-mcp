# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""An SSH-only QNX 8.0 machine (for example QNX's QNX OS 8.0 AMI on AWS Graviton: aarch64le, user qnxuser):
registration, refusal of the VM lifecycle tools, a cross-build for its architecture, a crash with a verified
backtrace, a background run, file transfer, processes, logs, and unregistration.

Set QNX_MCP_E2E_SSH_HOST=<address of the machine>. The key is QNX_MCP_SSH_KEY (default ~/.ssh/id_ed25519) and its
public half must be authorised for the login user on the machine. Optional:
  QNX_MCP_E2E_SSH_NAME         target name to register (default: ami1; QNX_MCP_ALLOW_TARGETS must allow it)
  QNX_MCP_E2E_SSH_ARCH         aarch64le (default) or x86_64
  QNX_MCP_E2E_SSH_USER         login user (default: qnxuser)
  QNX_MCP_E2E_SSH_PORT         default 22
  QNX_MCP_E2E_SSH_FINGERPRINT  SHA256:... of the machine's host key (ssh-keygen -l -f key.pub, or the instance console
                               output). Without it, QNX_MCP_TRUST_FIRST_USE=1 must be set (pins the first key; weaker).
The server under test gets QNX_MCP_SSH_HOSTS=<host> from this file, as the operator would set it. Needs the QNX SDP on this
host. The programs are built here and run on the machine; QNX_MCP_RUN_AS must not be set (the login is not root).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest

from helpers import mcp_client, run_async

HOST = os.environ.get("QNX_MCP_E2E_SSH_HOST")
NAME = os.environ.get("QNX_MCP_E2E_SSH_NAME", "ami1")
ARCH = os.environ.get("QNX_MCP_E2E_SSH_ARCH", "aarch64le")
USER = os.environ.get("QNX_MCP_E2E_SSH_USER", "qnxuser")
PORT = int(os.environ.get("QNX_MCP_E2E_SSH_PORT", "22"))
FINGERPRINT = os.environ.get("QNX_MCP_E2E_SSH_FINGERPRINT")
pytestmark = [pytest.mark.target, pytest.mark.skipif(not HOST, reason="set QNX_MCP_E2E_SSH_HOST to an SSH-only QNX 8.0 machine")]
DEMO = Path(__file__).resolve().parents[1] / "examples" / "crash_demo"
TICKER = ('#include <stdio.h>\n#include <unistd.h>\nint main(void){ for (int i = 0;; i++) { printf("tick %d\\n", i); fflush(stdout); usleep(200000); } }\n')
ENV = {"QNX_MCP_SSH_HOSTS": HOST or ""}
# Bad fingerprint of the right shape: SHA256 of nothing a real machine presents.
WRONG_FINGERPRINT = "SHA256:" + "A" * 43


def _crash_line() -> int:
    for n, line in enumerate((DEMO / "crash.c").read_text().splitlines(), 1):
        if "CRASH-LINE" in line:
            return n
    raise AssertionError("marker missing")


async def _register(c, name: str = NAME, **kw):
    args = dict(name=name, host=HOST, arch=ARCH, user=USER, port=PORT)
    if FINGERPRINT:
        args["host_key_fingerprint"] = FINGERPRINT
    args.update(kw)
    return await c.call("target_register", **args)


def test_register_refusals_and_lifecycle_tools(tmp_path: Path):
    async def go():
        async with mcp_client(ENV) as c:
            # Not an allowed host: refused before any connection, whatever the agent says.
            err, r = await c.call("target_register", name=NAME, host="not-allowed.invalid", arch=ARCH)
            assert err and r["error"]["code"] == "ssh_host_not_allowed", r
            if FINGERPRINT:
                err, r = await _register(c, host_key_fingerprint=WRONG_FINGERPRINT)
                assert err and r["error"]["code"] == "host_key_mismatch", r
                err, lst = await c.call("target_list")
                assert not err and NAME not in {t["name"] for t in lst["targets"]}, lst  # a refused registration leaves nothing
            err, r = await _register(c, arch="x86_64" if ARCH == "aarch64le" else "aarch64le")
            assert err and r["error"]["code"] == "arch_mismatch", r
            err, st = await _register(c)
            assert not err and st["registered"] is True and st["kind"] == "ssh" and st["state"] == "running" and st["ssh"] == "ok", st
            assert st["arch"] == ARCH and st["host"] == HOST and st["login_user"] == USER and st["port"] == PORT, st
            assert st["host_key_policy"] == ("fingerprint" if FINGERPRINT else "first_use"), st
            print("\n--- target_register", {k: st.get(k) for k in ("name", "kind", "state", "arch", "machine", "uname", "login_user", "dumper_running", "dumper_args", "core_naming")})
            err, again = await _register(c)
            assert err and again["error"]["code"] == "target_exists", again
            err, lst = await c.call("target_list")
            row = next(t for t in lst["targets"] if t["name"] == NAME)
            assert not err and row["kind"] == "ssh" and row["state"] == "registered", row
            err, st2 = await c.call("target_status", name=NAME)
            assert not err and st2["kind"] == "ssh" and st2["state"] == "running" and st2["machine"], st2
            for tool, extra in (("target_start", {}), ("target_stop", {}), ("target_destroy", {"confirm": NAME})):
                err, r = await c.call(tool, name=NAME, **extra)
                assert err and r["error"]["code"] == "target_kind_unsupported", (tool, r)
            err, st3 = await c.call("target_status", name=NAME)
            assert not err and st3["state"] == "running", st3  # the refusals changed nothing
            err, un = await c.call("target_unregister", name=NAME, confirm=NAME)
            assert not err and un["unregistered"] is True, un
            err, r = await c.call("target_status", name=NAME)
            assert err and r["error"]["code"] == "target_not_found", r
    run_async(go())


def test_crash_background_run_files_and_inspection(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client(ENV, log) as c:
            err, st = await _register(c)
            assert not err, st
            try:
                err, b = await c.call("build", path=str(DEMO), output=str(tmp_path / "crash_demo"), target=NAME)
                assert not err and b["ok"] and b["error_count"] == 0, b
                art = b["artifacts"][0]
                assert art["arch"] == ARCH and art["qnx"] is True and art["debug_info"] is True and art["build_id"], art
                assert f"-Vgcc_nto{ARCH}" in b["steps"][0]["argv"], b["steps"][0]["argv"]
                err, r = await c.call("build", path=str(DEMO), output=str(tmp_path / "wrong"), target=NAME, arch="x86_64" if ARCH == "aarch64le" else "aarch64le")
                assert err and r["error"]["code"] == "arch_mismatch", r

                err, r = await c.call("deploy_and_run", target=NAME, binary=art["path"], timeout_s=20)
                assert not err and r["outcome"] == "crashed" and r["signal_name"] == "SIGSEGV", r
                assert "imu: average offset 6" in r["stdout"] and r["binary_sha256"] == hashlib.sha256(Path(art["path"]).read_bytes()).hexdigest(), r
                assert r["run_as"] is None and r["group_clean"], r
                print("\n--- deploy_and_run", {k: r.get(k) for k in ("run_id", "outcome", "signal_name", "dumper", "core_path", "dumper_core_path", "core_ready_ms", "next_step")})
                err, cr = await c.call("get_crash", target=NAME, run_id=r["run_id"])
                assert not err, cr
                ff = cr["faulting_frame"]
                assert (ff["function"], Path(ff["file"]).name, ff["line"]) == ("read_calibration", "crash.c", _crash_line()), ff
                assert [f["function"] for f in cr["frames"][:3]] == ["read_calibration", "average_offset", "main"], cr["frames"][:3]
                assert cr["fault_address"] == "0x0" and cr["signal_name"] == "SIGSEGV", cr

                ticker = tmp_path / "ticker.c"
                ticker.write_text(TICKER)
                err, tb = await c.call("build", path=str(ticker), target=NAME)
                assert not err and tb["ok"], tb
                err, rs = await c.call("run_start", target=NAME, binary=tb["artifacts"][0]["path"], timeout_s=60)
                assert not err and rs["state"] == "running" and rs["pid"], rs
                await asyncio.sleep(1.0)
                err, p1 = await c.call("run_poll", target=NAME, run_id=rs["run_id"], wait_s=2)
                assert not err and p1["state"] == "running" and p1["stdout"].startswith("tick 0\n"), p1
                err, pr = await c.call("processes", target=NAME, name="ticker")
                assert not err and pr["returned"] == 1 and pr["processes"][0]["pid"] == rs["pid"], pr
                err, lg = await c.call("logs", target=NAME, limit=5)
                assert not err, lg
                err, sp = await c.call("run_stop", target=NAME, run_id=rs["run_id"])
                assert not err and sp["state"] == "finished" and sp["outcome"] == "stopped", sp
                err, pr2 = await c.call("processes", target=NAME, name="ticker")
                assert not err and pr2["returned"] == 0, pr2

                data = os.urandom(100_000)
                src = tmp_path / "blob.bin"
                src.write_bytes(data)
                remote = f"/tmp/qnx-mcp-test/{os.getpid()}-{int(time.time())}/blob.bin"
                err, put = await c.call("file_put", target=NAME, local_path=str(src), remote_path=remote)
                assert not err and put["sha256"] == hashlib.sha256(data).hexdigest(), put
                err, ls = await c.call("file_list", target=NAME, path=os.path.dirname(remote))
                assert not err and [e["name"] for e in ls["entries"]] == ["blob.bin"], ls
                dest = tmp_path / "back.bin"
                err, got = await c.call("file_get", target=NAME, remote_path=remote, local_path=str(dest))
                assert not err and dest.read_bytes() == data and got["sha256"] == put["sha256"], got
                err, bad = await c.call("file_put", target=NAME, local_path=str(src), remote_path="/proc/boot/x")
                assert err and bad["error"]["code"] == "path_not_allowed", bad

                err, env = await c.call("environment_check")
                by = {x["check"]: x for x in env["checks"]}
                assert not err and by["ssh_hosts"]["ok"] and HOST in by["ssh_hosts"]["detail"], env
            finally:
                await c.call("target_unregister", name=NAME, confirm=NAME)
    try:
        run_async(go())
    finally:
        print("\n--- MCP call log\n" + "\n".join(log))
