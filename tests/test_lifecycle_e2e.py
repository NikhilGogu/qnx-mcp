# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Target lifecycle through MCP (mkqnximage driven by the server), host key pinning, locking.

QNX_MCP_E2E_CREATE=<new target name> builds, boots, stops, restarts and destroys a fresh
target (and checks that a failed create is cleaned up). QNX_MCP_E2E_TARGET=<running target>
checks that inspection works while a run is active.
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
TARGETS_DIR = Path(os.environ.get("QNX_MCP_TARGETS_DIR", str(Path.home() / ".qnx-mcp" / "targets")))
STATE_DIR = Path(os.environ.get("QNX_MCP_STATE_DIR", str(Path.home() / ".qnx-mcp" / "state")))
DEMO = Path(__file__).resolve().parents[1] / "examples" / "crash_demo"
HANG = '#include <unistd.h>\nint main(void){ for (;;) sleep(1); }\n'


@pytest.mark.skipif(not NEW, reason="set QNX_MCP_E2E_CREATE to a new target name")
def test_failed_create_is_cleaned_up():
    name = f"{NEW}f"

    async def go():
        async with mcp_client({}) as c:
            err, cr = await c.call("target_create", name=name, cpus=1, boot_timeout_s=1)
            assert err and cr["error"]["code"] == "timeout" and "removed" in cr["error"]["details"]["cleanup"], cr
            print("\n--- target_create with boot_timeout_s=1\n" + json.dumps({k: cr["error"][k] for k in ("code", "message")} | {"cleanup": cr["error"]["details"]["cleanup"]}, indent=1))
            err, ls = await c.call("target_list")
            assert name not in [t["name"] for t in ls["targets"]]
    run_async(go())
    assert not (TARGETS_DIR / name).exists()


@pytest.mark.skipif(not NEW, reason="set QNX_MCP_E2E_CREATE to a new target name")
def test_create_stop_start_destroy(tmp_path: Path):
    log: list[str] = []

    async def go():
        async with mcp_client({}, log) as c, mcp_client({}) as c2:
            t0 = time.monotonic()
            err, cr = await c.call("target_create", name=NEW, cpus=1, options=[f"--hostname={NEW}"], boot_timeout_s=300)
            assert not err and cr["state"] == "running" and cr["ssh"] == "ok" and cr["dumper_running"] is True, cr
            assert f"QNX {NEW} 8.0.0" in cr["uname"] and cr["host_key_policy"] == "image", cr
            assert cr["core_naming"] == "sequential" and cr["dumper_args"].endswith(" -n"), cr
            print(f"\n--- target_create {time.monotonic() - t0:.0f}s\n" + json.dumps({k: cr[k] for k in ("name", "state", "ip", "uname", "dumper_args", "core_naming", "host_key_policy")}, indent=1))
            pins = (STATE_DIR / "targets" / NEW / "known_hosts").read_text().splitlines()
            image = [" ".join(p.read_text().split()[:2]) for p in sorted((TARGETS_DIR / NEW / "local" / "misc_files").glob("ssh_host_*_key.pub"))]
            assert sorted(pins) == sorted(f"qnx-mcp-{NEW} {k}" for k in image) and len(pins) == 2, (pins, image)

            err, again = await c.call("target_create", name=NEW)
            assert err and again["error"]["code"] == "target_exists", again

            # Two runs of the same program crash at the same time: distinct cores, both verified.
            err, b = await c.call("build", path=str(DEMO), output=str(tmp_path / "crash_demo"))
            exe = b["artifacts"][0]["path"]
            (ra, a), (rb, bb) = await asyncio.gather(c.call("deploy_and_run", target=NEW, binary=exe), c2.call("deploy_and_run", target=NEW, binary=exe))
            assert not ra and not rb and a["outcome"] == bb["outcome"] == "crashed", (a, bb)
            assert a["core_path"] != bb["core_path"] and a["pid"] != bb["pid"], (a, bb)
            for r in (a, bb):
                err, cr_ = await c.call("get_crash", target=NEW, run_id=r["run_id"])
                assert not err and cr_["provenance"]["verified"] and cr_["faulting_frame"]["function"] == "read_calibration", cr_
                assert cr_["provenance"]["core_pid"] == r["pid"] and cr_["provenance"]["pid_matches_run"] is True, cr_["provenance"]
            print("\n--- two concurrent crashes of crash_demo on a sequential-naming target\n" + json.dumps(
                [{k: r[k] for k in ("run_id", "pid", "dumper_core_path", "core_path")} for r in (a, bb)], indent=1))

            # target_stop (exclusive lock) waits for a run in progress (shared lock).
            (tmp_path / "hang.c").write_text(HANG)
            err, hb = await c.call("build", path=str(tmp_path / "hang.c"))

            async def run_a():
                return await c.call("deploy_and_run", target=NEW, binary=hb["artifacts"][0]["path"], timeout_s=4), time.monotonic()

            async def stop_b():
                await asyncio.sleep(1.0)
                t = time.monotonic()
                res = await c2.call("target_stop", name=NEW)
                return res, time.monotonic() - t, time.monotonic()

            ((ea, ha), a_end), ((eb, sb), b_took, b_end) = await asyncio.gather(run_a(), stop_b())
            assert not ea and ha["outcome"] == "timeout", ha
            assert not eb and sb["state"] == "stopped" and sb["was_running"] is True and b_end >= a_end and b_took > 2.0, (sb, b_took)
            print(f"\n--- target_stop issued 1 s into a 4 s run waited {b_took:.2f} s and stopped the target after the run ended (run outcome {ha['outcome']})")

            err, down = await c.call("deploy_and_run", target=NEW, binary=hb["artifacts"][0]["path"])
            assert err and down["error"]["code"] == "target_down", down

            t1 = time.monotonic()
            err, st = await c.call("target_start", name=NEW, boot_timeout_s=240)
            assert not err and st["state"] == "running" and st["ssh"] == "ok" and st["host_key_policy"] == "image", st
            print(f"\n--- target_start {time.monotonic() - t1:.0f}s: ssh ok with StrictHostKeyChecking=yes against the keys pinned at creation")
            assert (STATE_DIR / "targets" / NEW / "known_hosts").read_text().splitlines() == pins

            err, ds = await c.call("target_destroy", name=NEW, confirm=NEW)
            assert not err and ds["destroyed"] and ds["was_running"], ds
            print("\n--- target_destroy\n" + json.dumps(ds, indent=1))
            err, ls = await c.call("target_list")
            assert NEW not in [t["name"] for t in ls["targets"]]

    run_async(go())
    assert not (TARGETS_DIR / NEW).exists()
    left = sorted(p.name for p in (STATE_DIR / "targets" / NEW).iterdir())
    assert set(left) <= {"lock", "records.lock", "mkqnximage.log"}, left
    print("\n--- MCP call log\n" + "\n".join(log))


@pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
def test_inspection_during_a_run(tmp_path: Path):
    """Two server processes (two agents) share one target: runs hold a shared lock, so pidin works meanwhile."""
    (tmp_path / "hang.c").write_text(HANG)

    async def go():
        async with mcp_client({}) as a, mcp_client({}) as b:
            err, hb = await a.call("build", path=str(tmp_path / "hang.c"))
            assert not err

            async def agent_a():
                return await a.call("deploy_and_run", target=TARGET, binary=hb["artifacts"][0]["path"], timeout_s=3)

            async def agent_b():
                await asyncio.sleep(1.0)
                t = time.monotonic()
                res = await b.call("processes", target=TARGET, name="hang")
                return res, time.monotonic() - t

            (ea, ra), ((eb, pb), took) = await asyncio.gather(agent_a(), agent_b())
            assert not ea and ra["outcome"] == "timeout", ra
            assert not eb and pb["returned"] == 1 and took < 2.0, (pb, took)
            print(f"\n--- agent B listed agent A's running 'hang' (pid {pb['processes'][0]['pid']}) in {took:.2f} s while A's run was active")

    run_async(go())
