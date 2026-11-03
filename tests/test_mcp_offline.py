# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests without a running target: schemas, error objects, isolation."""

from __future__ import annotations

from pathlib import Path

from helpers import mcp_client, run_async

EXPECTED = {"target_list", "target_create", "target_start", "target_stop", "target_status", "build", "deploy_and_run",
            "get_crash", "processes", "logs", "environment_check"}


def _env(tmp: Path) -> dict[str, str]:
    return {"QNX_MCP_TARGETS_DIR": str(tmp / "targets"), "QNX_MCP_STATE_DIR": str(tmp / "state"), "QNX_MCP_ALLOW_TARGETS": "tb*"}


def _fake_target(tmp: Path, name: str) -> None:
    d = tmp / "targets" / name / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:01'\nOPT_RAM='1G'\nOPT_CPU='2'\n")


def test_tools_have_descriptions_and_schemas(tmp_path: Path):
    async def go():
        async with mcp_client(_env(tmp_path)) as c:
            tools = (await c.session.list_tools()).tools
            names = {t.name for t in tools}
            assert EXPECTED <= names
            for t in tools:
                assert t.description and len(t.description) > 60, t.name
                assert t.output_schema and t.output_schema.get("type") == "object", t.name
                assert t.annotations is not None, t.name
    run_async(go())


def test_structured_errors(tmp_path: Path):
    _fake_target(tmp_path, "tb9")
    _fake_target(tmp_path, "t0")

    async def go():
        async with mcp_client(_env(tmp_path)) as c:
            err, data = await c.call("target_status", name="tb1")
            assert err and data["error"]["code"] == "target_not_found" and data["error"]["hint"]
            err, data = await c.call("target_status", name="t0")
            assert err and data["error"]["code"] == "target_not_allowed"
            err, data = await c.call("target_list")
            assert not err and [t["name"] for t in data["targets"]] == ["tb9"]
            assert data["targets"][0]["state"] == "stopped"
            err, data = await c.call("processes", target="tb9")
            assert err and data["error"]["code"] == "target_down"
            err, data = await c.call("deploy_and_run", target="tb9", binary="/bin/ls")
            assert err and data["error"]["code"] == "arch_mismatch" or data["error"]["code"] == "not_a_qnx_binary"
            err, data = await c.call("target_create", name="tb9", options=["--ip=10.0.0.1"])
            assert err and data["error"]["code"] == "invalid_argument"
            err, data = await c.call("get_crash", target="tb9")
            assert err and data["error"]["code"] == "core_not_found"
    run_async(go())
