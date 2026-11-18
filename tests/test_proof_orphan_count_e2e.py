# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Proof test: what is left on the target after N timed-out runs of a program that hangs.

Baseline: `timeout 10 ssh root@target ./hang`, the plain way an agent or a script runs a program over ssh,
and the same with `ssh -tt` (a pty, which is the fairer variant). Ours: `deploy_and_run` with `timeout_s`.
Leftovers are counted with `pidin` on the target. A second program forks a child that calls `setsid()`;
README says qnx-mcp leaves that child behind (one per run), and this test counts it too.

First it settles with one plain run whether QNX sshd already kills the remote process when the ssh client
goes away. If the baseline leaves nothing, the test skips: there is nothing to prove.

Needs QNX_MCP_E2E_TARGET (a running target made by target_create, logged in as root) and the QNX SDP.
QNX_MCP_PROOF_RUNS (default 50) is the number of timed-out runs per case. QNX_MCP_PROOF_PARALLEL (default 5)
is how many baseline ssh runs go at once (they only save wall time; 1 runs them one after the other). QNX_MCP_PROOF_OUT, if set, is a
directory that receives orphan_count.json. See docs/PROOF_TESTS.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from helpers import mcp_client, run_async
from qnx_mcp.target import Target
from qnx_mcp.util import Config

TARGET = os.environ.get("QNX_MCP_E2E_TARGET")
pytestmark = pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
RUNS = int(os.environ.get("QNX_MCP_PROOF_RUNS", "50"))
PARALLEL = int(os.environ.get("QNX_MCP_PROOF_PARALLEL", "5"))  # baseline ssh runs at once; 1 runs them one after the other
TIMEOUT_S = 10  # the same limit for the baseline (`timeout 10 ssh ...`) and for qnx-mcp (`timeout_s`)

HANG = '#include <unistd.h>\n#include <stdio.h>\nint main(void){ puts("start"); fflush(stdout); for (;;) sleep(1); }\n'
# A daemon-like child: it starts its own session and closes its stdio, then hangs. The parent hangs too.
SETSID = r'''#include <stdio.h>
#include <unistd.h>
int main(void)
{
    pid_t child = fork();
    if (child == 0) {
        setsid();
        close(0); close(1); close(2);
        for (;;) sleep(1);
    }
    puts("start"); fflush(stdout);
    for (;;) sleep(1);
}
'''


def _pids(t: Target, name: str) -> list[int]:
    """Process ids on the target whose program is called `name` (pidin, one row per thread, so deduplicated)."""
    out = t.ssh(["pidin", "-p", name, "-F", "%a"], timeout=30, check=False).out
    return sorted({int(m) for m in re.findall(r"^\s*(\d+)\s*$", out, re.M)})


def _slay_all(t: Target, name: str) -> None:
    for _ in range(5):
        if not _pids(t, name):
            return
        t.ssh(["slay", "-f", "-s", "KILL", name], timeout=30, check=False)
        time.sleep(0.5)
    raise AssertionError(f"could not remove the test processes called {name}")


def _baseline_once(argv_prefix: list[str], opts: list[str], ip: str, remote: str) -> None:
    subprocess.run([*argv_prefix, *opts, f"root@{ip}", remote], capture_output=True, timeout=TIMEOUT_S + 30)


def _baseline(t: Target, opts: list[str], ip: str, remote: str, name: str, tty: bool, runs: int) -> dict:
    """`timeout 10 ssh [-tt] root@target remote`, `runs` times, then count what is left."""
    prefix = ["timeout", str(TIMEOUT_S), "ssh"] + (["-tt"] if tty else [])
    _baseline_once(prefix, opts, ip, remote)
    time.sleep(2)
    after_first = len(_pids(t, name))
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:  # parallel only to save wall time; each run is its own ssh and its own timeout
        list(ex.map(lambda _: _baseline_once(prefix, opts, ip, remote), range(runs - 1)))
    time.sleep(3)
    return {"runs": runs, "left_after_first_run": after_first, "left_after_all_runs": len(_pids(t, name))}


async def _ours(t: Target, binary: str, name: str, runs: int) -> dict:
    outcomes: dict[str, int] = {}
    async with mcp_client({}) as c:
        for _ in range(runs):
            err, r = await c.call("deploy_and_run", target=TARGET, binary=binary, timeout_s=TIMEOUT_S)
            assert not err, r
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    time.sleep(3)
    return {"runs": runs, "outcomes": outcomes, "left_after_all_runs": len(_pids(t, name))}


def test_orphan_count(tmp_path: Path):
    cfg = Config.from_env()
    t = Target(cfg, TARGET)
    ip = t.require_ip()
    opts = t._ssh_opts()  # the same key, pinned host key and ssh options qnx-mcp itself uses
    suffix = uuid.uuid4().hex[:6]
    remote_dir = f"/data/var/tmp/proof-orphans-{suffix}"
    names = {k: f"{k}_{suffix}" for k in ("hang_plain", "hang_tty", "hang_ours", "setsid_plain", "setsid_tty", "setsid_ours")}
    ours_paths: dict[str, str] = {}
    result: dict = {"runs_per_case": RUNS, "timeout_s": TIMEOUT_S, "target": TARGET, "uname": "", "cases": {}}

    async def build_all() -> None:
        async with mcp_client({}) as c:
            for prog, src in (("hang", HANG), ("setsid", SETSID)):
                (tmp_path / f"{prog}.c").write_text(src)
                for kind in ("plain", "tty", "ours"):
                    key = f"{prog}_{kind}"
                    err, b = await c.call("build", path=str(tmp_path / f"{prog}.c"), output=str(tmp_path / names[key]))
                    assert not err and b["artifacts"], b
                    ours_paths[key] = b["artifacts"][0]["path"]

    run_async(build_all())
    result["uname"] = t.ssh(["uname", "-a"], timeout=30).out.strip()
    t.ssh(["mkdir", "-p", remote_dir], timeout=30)
    remote = {k: f"{remote_dir}/{names[k]}" for k in names}
    for key in ("hang_plain", "hang_tty", "setsid_plain", "setsid_tty"):
        t.put(Path(ours_paths[key]), remote[key], timeout=120)
        t.ssh(["chmod", "755", remote[key]], timeout=30)

    try:
        for n in names.values():
            _slay_all(t, n)

        # Settle first: does sshd kill the remote process when the plain ssh client goes away?
        first = _baseline(t, opts, ip, remote["hang_plain"], names["hang_plain"], tty=False, runs=1)
        print(f"\nsettle: one plain `timeout {TIMEOUT_S} ssh root@target hang` leaves {first['left_after_first_run']} process(es)")
        result["settle_plain_one_run_left"] = first["left_after_first_run"]
        if first["left_after_first_run"] == 0:
            pytest.skip("QNX sshd ends the remote program when ssh goes away: the baseline leaves nothing, so there is nothing to prove")
        _slay_all(t, names["hang_plain"])

        # hang: the program that never exits
        result["cases"]["hang"] = {
            "baseline_plain_ssh": _baseline(t, opts, ip, remote["hang_plain"], names["hang_plain"], False, RUNS),
            "baseline_ssh_tt": _baseline(t, opts, ip, remote["hang_tty"], names["hang_tty"], True, RUNS),
            "qnx_mcp_deploy_and_run": run_async(_ours(t, ours_paths["hang_ours"], names["hang_ours"], RUNS)),
        }
        # setsid: the program forks a child that starts its own session (two processes per run in the baseline)
        result["cases"]["setsid"] = {
            "baseline_plain_ssh": _baseline(t, opts, ip, remote["setsid_plain"], names["setsid_plain"], False, RUNS),
            "baseline_ssh_tt": _baseline(t, opts, ip, remote["setsid_tty"], names["setsid_tty"], True, RUNS),
            "qnx_mcp_deploy_and_run": run_async(_ours(t, ours_paths["setsid_ours"], names["setsid_ours"], RUNS)),
        }
    finally:
        for n in names.values():
            try:
                _slay_all(t, n)
            except AssertionError:
                pass
        t.ssh(["rm", "-rf", remote_dir], timeout=60, check=False)
        out = os.environ.get("QNX_MCP_PROOF_OUT")
        if out and result["cases"]:
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out) / "orphan_count.json").write_text(json.dumps(result, indent=1) + "\n")
        print("\nPROOF orphan_count " + json.dumps(result))

    h, s = result["cases"]["hang"], result["cases"]["setsid"]
    # The claim: after N timed-out runs the plain ssh baseline leaves N programs and qnx-mcp leaves none.
    assert h["baseline_plain_ssh"]["left_after_all_runs"] == RUNS, h
    assert h["qnx_mcp_deploy_and_run"]["outcomes"] == {"timeout": RUNS}, h
    assert h["qnx_mcp_deploy_and_run"]["left_after_all_runs"] == 0, h
    # The limit README states: a descendant that calls setsid() escapes the process group, one per run.
    assert s["qnx_mcp_deploy_and_run"]["outcomes"] == {"timeout": RUNS}, s
    assert s["qnx_mcp_deploy_and_run"]["left_after_all_runs"] == RUNS, s
    assert s["baseline_plain_ssh"]["left_after_all_runs"] == 2 * RUNS, s
    # ssh -tt is reported, not asserted: a pty hang-up may or may not end the program, and that is sshd's behaviour, not ours.
