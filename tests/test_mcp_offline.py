# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests without a running target: schemas, error objects, annotations, isolation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from jsonschema.validators import validator_for

from helpers import mcp_client, run_async

EXPECTED = {"target_list", "target_create", "target_start", "target_stop", "target_destroy", "target_status", "build", "deploy_and_run",
            "run_start", "run_poll", "run_stop", "get_crash", "file_put", "file_get", "processes", "logs", "environment_check"}


def _env(tmp: Path, allow: str = "tb*") -> dict[str, str]:
    return {"QNX_MCP_TARGETS_DIR": str(tmp / "targets"), "QNX_MCP_STATE_DIR": str(tmp / "state"), "QNX_MCP_ALLOW_TARGETS": allow}


def _fake_target(tmp: Path, name: str) -> Path:
    d = tmp / "targets" / name / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:01'\nOPT_RAM='1G'\nOPT_CPU='2'\n")
    return d.parent


def _validate(schema: dict, data: dict) -> None:
    cls = validator_for(schema)
    cls.check_schema(schema)
    cls(schema).validate(data)


def _hint(a, name: str):
    return getattr(a, name, None)


def test_tools_have_descriptions_schemas_and_annotations(tmp_path: Path):
    async def go():
        async with mcp_client(_env(tmp_path)) as c:
            tools = {t.name: t for t in (await c.session.list_tools()).tools}
            assert EXPECTED == set(tools)
            for t in tools.values():
                assert t.description and len(t.description) > 60, t.name
                s = t.output_schema
                assert s and s.get("type") == "object" and len(s.get("anyOf", [])) == 2, t.name
                assert t.annotations is not None and _hint(t.annotations, "open_world_hint") is False, t.name
            a = {n: t.annotations for n, t in tools.items()}
            assert _hint(a["build"], "idempotent_hint") is False and _hint(a["build"], "destructive_hint") is True
            for n in ("target_list", "target_status", "get_crash", "processes", "logs", "run_poll", "environment_check"):
                assert _hint(a[n], "read_only_hint") is True, n
            for n in ("target_stop", "target_destroy", "deploy_and_run", "run_start", "run_stop", "file_put"):
                assert _hint(a[n], "destructive_hint") is True and _hint(a[n], "read_only_hint") is False, n
    run_async(go())


def test_error_results_match_declared_schemas(tmp_path: Path):
    _fake_target(tmp_path, "tb9")
    _fake_target(tmp_path, "t0")

    async def go():
        async with mcp_client(_env(tmp_path)) as c:
            schemas = {t.name: t.output_schema for t in (await c.session.list_tools()).tools}
            cases = [
                ("target_status", {"name": "tb1"}, "target_not_found"),
                ("target_status", {"name": "t0"}, "target_not_allowed"),
                ("processes", {"target": "tb9"}, "target_down"),
                ("logs", {"target": "tb9"}, "target_down"),
                ("deploy_and_run", {"target": "tb9", "binary": "/bin/ls"}, "not_a_qnx_binary"),
                ("run_start", {"target": "tb9", "binary": "/bin/ls"}, "not_a_qnx_binary"),
                ("deploy_and_run", {"target": "tb9", "binary": "relative/path"}, "invalid_argument"),
                ("target_create", {"name": "tb9", "options": ["--ip=10.0.0.1"]}, "invalid_argument"),
                ("target_create", {"name": "tb5", "cpus": 9}, "invalid_argument"),  # rejected by the input schema itself
                ("get_crash", {"target": "tb9"}, "core_not_found"),
                ("get_crash", {"target": "tb9", "core_path": "/tmp/x.core"}, "invalid_argument"),
                ("run_poll", {"target": "tb9", "run_id": "r-000000000000"}, "invalid_argument"),
                ("run_stop", {"target": "tb9", "run_id": "nonsense"}, "invalid_argument"),
                ("file_put", {"target": "tb9", "local_path": "/etc/hostname", "remote_path": "/etc/passwd"}, "path_not_allowed"),
                ("file_put", {"target": "tb9", "local_path": "/etc/hostname", "remote_path": "/tmp/qnx-mcp/bin/x"}, "path_not_allowed"),
                ("file_get", {"target": "tb9", "remote_path": "/tmp/$(id)"}, "invalid_argument"),
                ("file_put", {"target": "tb9", "local_path": "/etc/hostname", "remote_path": "/tmp//qnx-mcp/bin/x"}, "invalid_argument"),
                ("run_poll", {"target": "tb9", "run_id": "r-000000000000", "max_kb": 4096}, "invalid_argument"),
                ("target_destroy", {"name": "tb9", "confirm": "tb8"}, "invalid_argument"),
                ("target_destroy", {"name": "t0", "confirm": "t0"}, "target_not_allowed"),
                ("build", {"path": "/nonexistent/dir"}, "invalid_argument"),
            ]
            for tool, args, code in cases:
                err, data = await c.call(tool, **args)
                assert err and data.get("ok") is False and data["error"]["code"] == code, (tool, args, data)
                assert data["error"]["message"], (tool, data)
                _validate(schemas[tool], data)
            err, data = await c.call("target_create", name="tb5", cpus=9)
            assert data["error"]["details"]["errors"][0]["field"] == "cpus"
            err, data = await c.call("target_list")
            assert not err and [t["name"] for t in data["targets"]] == ["tb9"] and data["targets"][0]["state"] == "stopped"
            _validate(schemas["target_list"], data)
            err, data = await c.call("environment_check")
            _validate(schemas["environment_check"], data)
    run_async(go())


def test_unset_allowlist_refuses_everything(tmp_path: Path):
    _fake_target(tmp_path, "tb9")

    async def go():
        async with mcp_client(_env(tmp_path, allow="")) as c:
            err, data = await c.call("target_list")
            assert not err and data["targets"] == [] and data["allowlist"] == []
            err, data = await c.call("target_status", name="tb9")
            assert err and data["error"]["code"] == "target_not_allowed" and "QNX_MCP_ALLOW_TARGETS" in data["error"]["hint"]
    run_async(go())


def test_destroy_refuses_symlinked_target(tmp_path: Path):
    real = _fake_target(tmp_path / "other", "t0")
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "tb7").symlink_to(real)

    async def go():
        async with mcp_client(_env(tmp_path)) as c:
            err, data = await c.call("target_destroy", name="tb7", confirm="tb7")
            assert err and data["error"]["code"] == "target_not_allowed", data
    run_async(go())
    assert (real / "local" / "options").is_file()


def test_check_cli_reports_failures_with_fixes(tmp_path: Path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "QNX_MCP_SDP_ENV": str(tmp_path / "missing-qnxsdp-env.sh"),
           **_env(tmp_path, allow="")}
    p = subprocess.run([sys.executable, "-m", "qnx_mcp.server", "--check"], env=env, capture_output=True, text=True, timeout=60)
    lines = p.stdout.splitlines()
    assert p.returncode == 1, p.stdout + p.stderr
    assert any(line.startswith("FAIL  qnx_sdp") for line in lines)
    assert any(line.startswith("FAIL  allowlist") for line in lines)
    assert any("fix: Set QNX_MCP_ALLOW_TARGETS" in line for line in lines)
    assert lines[-1].startswith("environment_check: FAILED")
