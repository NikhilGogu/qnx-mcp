# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Target lifecycle through MCP (mkqnximage driven by the server) and cross-process locking.

QNX_MCP_E2E_CREATE=<new target name> builds, boots, stops and restarts a fresh target.
QNX_MCP_E2E_TARGET=<running target> runs the two-agent locking test.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from helpers import mcp_client, run_async

NEW = os.environ.get("QNX_MCP_E2E_CREATE")
TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
HANG = '#include <unistd.h>\nint main(void){ for (;;) sleep(1); }\n'


@pytest.mark.skipif(not NEW, reason="set QNX_MCP_E2E_CREATE to a new target name")
def test_create_stop_start(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c:
            t0 = time.monotonic()
            err, cr = await c.call("target_create", name=NEW, cpus=1, options=[f"--hostname={NEW}"], boot_timeout_s=300)
            assert not err and cr["state"] == "running" and cr["ssh"] == "ok" and cr["dumper_running"] is True, cr
            assert f"QNX {NEW} 8.0.0" in cr["uname"], cr["uname"]
            print(f"\n--- target_create {time.monotonic() - t0:.0f}s\n" + json.dumps({k: cr[k] for k in ("name", "state", "ip", "mac", "uname", "dumper_running")}, indent=1))
            err, again = await c.call("target_create", name=NEW)
            assert err and again["error"]["code"] == "target_exists", again
            err, sp = await c.call("target_stop", name=NEW)
            assert not err and sp["state"] == "stopped" and sp["was_running"] is True, sp
            (tmp_path / "hang.c").write_text(HANG)
            err, hb = await c.call("build", path=str(tmp_path / "hang.c"))
            err, down = await c.call("deploy_and_run", target=NEW, binary=hb["artifacts"][0]["path"])
            assert err and down["error"]["code"] == "target_down", down
            print("\n--- deploy_and_run on stopped target\n" + json.dumps(down, indent=1))
            t1 = time.monotonic()
            err, st = await c.call("target_start", name=NEW, boot_timeout_s=240)
            assert not err and st["state"] == "running" and st["ssh"] == "ok", st
            print(f"\n--- target_start {time.monotonic() - t1:.0f}s state={st['state']} ip={st['ip']}")
            err, ls = await c.call("target_list")
            assert not err and NEW in [t["name"] for t in ls["targets"]], ls
            print("--- target_list names:", [t["name"] for t in ls["targets"]], "allowlist:", ls["allowlist"])
            err, sp = await c.call("target_stop", name=NEW)
            assert not err and sp["state"] == "stopped"

    run_async(go())
    print("\n--- MCP call log\n" + "\n".join(log))


@pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
def test_two_agents_are_serialised(tmp_path: Path):
    """Two server processes (two agents) share one target: the per-target flock orders them."""
    (tmp_path / "hang.c").write_text(HANG)

    async def go():
        async with mcp_client({}) as a, mcp_client({}) as b:
            err, hb = await a.call("build", path=str(tmp_path / "hang.c"))
            assert not err

            async def agent_a():
                t = time.monotonic()
                res = await a.call("deploy_and_run", target=TARGET, binary=hb["artifacts"][0]["path"], timeout_s=3)
                return res, time.monotonic() - t, time.monotonic()

            async def agent_b():
                await asyncio.sleep(1.0)
                t = time.monotonic()
                res = await b.call("deploy_and_run", target=TARGET, binary=hb["artifacts"][0]["path"], timeout_s=0.5)
                return res, time.monotonic() - t, time.monotonic()

            (ra, da, ea), (rb, db, eb) = await asyncio.gather(agent_a(), agent_b())
            assert not ra[0] and ra[1]["outcome"] == "timeout", ra
            assert not rb[0] and rb[1]["outcome"] == "timeout", rb
            assert eb > ea and db > 2.0, (da, db)
            print(f"\n--- agent A run {da:.2f}s, agent B (started 1 s later) waited for the lock and finished after A: B took {db:.2f}s")

    run_async(go())
