# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Non-root runs (QNX_MCP_RUN_AS), offline: configuration parsing, the runner argv, fail-closed behaviour and
the privilege report. What the runner does on a QNX target is covered by test_nonroot_e2e.py (needs a target)."""

from __future__ import annotations

import contextlib
import json
import types
from pathlib import Path

import pytest

from qnx_mcp import ops
from qnx_mcp.abilities import TOOL_ABILITIES, privileges
from qnx_mcp.target import Target
from qnx_mcp.util import Config, QnxMcpError, parse_run_as

from helpers import write_elf


@pytest.mark.parametrize("value,want", [("", None), ("  ", None), ("1000:1000", (1000, 1000)), (" 100:200 ", (100, 200)),
                                        ("2147483646:1", (2147483646, 1))])
def test_run_as_accepts_numeric_ids(value, want):
    assert parse_run_as(value) == (want, None)


@pytest.mark.parametrize("value", ["0:0", "0:1000", "1000:0", "root", "qnxuser:qnxuser", "1000", "1000:", ":1000", "-1:5", "1000:1000:1",
                                   "1e3:1", "2147483647:1", "1000 :1000", "1000:\n1000"])
def test_run_as_refuses_root_names_and_junk(value):
    ids, err = parse_run_as(value)
    assert ids is None and err and "QNX_MCP_RUN_AS" in err


def _cfg(tmp_path: Path, **env: str) -> Config:
    return Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "s"),
                            "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path), **env})


def test_config_reads_run_as(tmp_path):
    assert _cfg(tmp_path).run_as is None and _cfg(tmp_path).run_as_error is None
    assert _cfg(tmp_path, QNX_MCP_RUN_AS="1000:100").run_as == (1000, 100)
    bad = _cfg(tmp_path, QNX_MCP_RUN_AS="0:0")
    assert bad.run_as is None and bad.run_as_error


def test_run_as_argv(tmp_path):
    assert ops._run_as_argv(_cfg(tmp_path)) == []
    assert ops._run_as_argv(_cfg(tmp_path, QNX_MCP_RUN_AS="1000:100")) == ["-u", "1000:100"]
    with pytest.raises(QnxMcpError) as e:
        ops._run_as_argv(_cfg(tmp_path, QNX_MCP_RUN_AS="root"))
    assert e.value.code == "config_error" and "QNX_MCP_RUN_AS" in e.value.message


RUNNER_REPORT = {"runner": "qnx-mcp-run", "version": "x", "pid": 77, "exec_error": None, "exited": True, "exit_code": 0, "signaled": False,
                 "signal": None, "signal_name": None, "core_expected": False, "timed_out": False, "disconnected": False,
                 "runner_stopped_by": None, "duration_ms": 1, "group_clean": True, "capture_complete": True, "stdout": "", "stderr": "",
                 "stdout_bytes": 0, "stdout_truncated": False, "stderr_bytes": 0, "stderr_truncated": False, "dumper": "not_applicable",
                 "dumper_error": None, "core_ready_ms": -1, "core_path": None, "dumper_core_path": None, "core_move_error": None, "warnings": []}


@pytest.fixture
def fake_target(tmp_path, monkeypatch):
    """A target whose ssh calls are recorded instead of run; the program is a synthetic QNX ELF file."""
    d = tmp_path / "targets" / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:01'\n")
    binary = write_elf(tmp_path / "app", interpreter="/usr/lib/ldqnx-64.so.2")
    calls: list[list[str]] = []
    report = dict(RUNNER_REPORT)

    def fake_ssh(self, argv, **kw):
        calls.append([str(a) for a in argv])
        if "-B" in argv:
            out = {"runner": "qnx-mcp-run", "version": "x", "started": True, "runner_pid": 5, "pid": 6, "error": None}
        else:
            out = report
        return types.SimpleNamespace(returncode=0, timed_out=False, out=json.dumps(out), err="", truncated=False)

    monkeypatch.setattr(Target, "require_ip", lambda self: "10.0.0.1")
    monkeypatch.setattr(Target, "ssh", fake_ssh)
    monkeypatch.setattr(Target, "lock", lambda self, exclusive: contextlib.nullcontext())
    monkeypatch.setattr(ops, "_install_program", lambda t, ip, prog, remote_run: "/tmp/qnx-mcp/bin/qnx-mcp-run-0")
    monkeypatch.setattr(ops, "_reconcile_starts", lambda t, ip, exclude: None)
    return types.SimpleNamespace(binary=binary, calls=calls, report=report)


def _runner_argv(calls):
    argv = calls[-1]
    return argv[: argv.index("--")]


def test_runs_pass_u_only_when_configured(tmp_path, fake_target):
    r = ops.deploy_and_run(_cfg(tmp_path), "tb1", binary=fake_target.binary)
    assert r["outcome"] == "exited" and r["run_as"] is None
    assert "-u" not in _runner_argv(fake_target.calls)

    cfg = _cfg(tmp_path, QNX_MCP_RUN_AS="1000:100")
    r = ops.deploy_and_run(cfg, "tb1", binary=fake_target.binary)
    head = _runner_argv(fake_target.calls)
    assert head[head.index("-u") + 1] == "1000:100" and r["run_as"] == {"uid": 1000, "gid": 100}
    assert head.index("-u") < head.index("-R")

    r = ops.run_start(cfg, "tb1", binary=fake_target.binary)
    head = _runner_argv(fake_target.calls)
    assert "-B" in head and head[head.index("-u") + 1] == "1000:100" and r["run_as"] == {"uid": 1000, "gid": 100}
    rec = ops.load_record(Target(cfg, "tb1"), r["run_id"])
    assert rec["run_as"] == {"uid": 1000, "gid": 100}


def test_bad_run_as_refuses_before_anything_runs(tmp_path, fake_target):
    cfg = _cfg(tmp_path, QNX_MCP_RUN_AS="0:0")
    for fn in (ops.deploy_and_run, ops.run_start):
        with pytest.raises(QnxMcpError) as e:
            fn(cfg, "tb1", binary=fake_target.binary)
        assert e.value.code == "config_error"
    assert fake_target.calls == []


def test_failed_drop_is_reported_and_the_program_did_not_run(tmp_path, fake_target):
    fake_target.report.update(exec_error="could not drop privileges (-u): Operation not permitted", exited=False, exit_code=None)
    r = ops.deploy_and_run(_cfg(tmp_path, QNX_MCP_RUN_AS="1000:100"), "tb1", binary=fake_target.binary)
    assert r["outcome"] == "exec_failed" and "QNX_MCP_RUN_AS" in r["next_step"]


def test_privilege_report_names_every_tool(tmp_path):
    from qnx_mcp.server import mcp
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert set(TOOL_ABILITIES) == names
    for name, a in TOOL_ABILITIES.items():
        assert a["side"] in ("host", "target") and a["identity"] and isinstance(a["needs"], list), name
        if a["side"] == "host":
            assert a["identity"] == "server_user", name
    root = privileges(_cfg(tmp_path))
    assert root["login_user"] == "root" and root["program_identity"] == "login_user" and root["run_as"] is None
    drop = privileges(_cfg(tmp_path, QNX_MCP_RUN_AS="1000:100"))
    assert drop["program_identity"] == "run_as" and drop["run_as"] == {"uid": 1000, "gid": 100}
    assert {t["tool"] for t in drop["tools"] if t["identity"] == "agent_program"} == {"deploy_and_run", "run_start"}
    assert all(t["verified_on_target"] is False for t in drop["tools"] if t["identity"] == "agent_program")
    bad = privileges(_cfg(tmp_path, QNX_MCP_RUN_AS="root"))
    assert bad["program_identity"] == "refused" and "QNX_MCP_RUN_AS" in bad["error"]


def test_environment_check_reports_program_identity(tmp_path):
    def check(cfg):
        return {c["check"]: c for c in ops.environment_check(cfg)["checks"]}["program_identity"]
    assert check(_cfg(tmp_path))["ok"] and "root" in check(_cfg(tmp_path))["detail"]
    c = check(_cfg(tmp_path, QNX_MCP_RUN_AS="1000:100"))
    assert c["ok"] and "uid 1000 gid 100" in c["detail"]
    c = check(_cfg(tmp_path, QNX_MCP_RUN_AS="0:0"))
    assert not c["ok"] and c["fix"]
    assert "privileges" in ops.environment_check(_cfg(tmp_path))
