# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""MCP-level tests without a running target: schemas, error objects, annotations, isolation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from jsonschema.validators import validator_for

from helpers import mcp_client, run_async, write_elf

EXPECTED = {"target_list", "target_create", "target_start", "target_stop", "target_destroy", "target_status", "build", "deploy_and_run",
            "run_start", "run_poll", "run_stop", "get_crash", "file_put", "file_get", "file_list", "processes", "process_stop", "logs",
            "environment_check", "target_register", "target_unregister"}


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
            for n in ("target_list", "target_status", "get_crash", "processes", "logs", "run_poll", "environment_check", "file_list"):
                assert _hint(a[n], "read_only_hint") is True, n
            for n in ("target_stop", "target_destroy", "target_unregister", "deploy_and_run", "run_start", "run_stop", "file_put", "process_stop"):
                assert _hint(a[n], "destructive_hint") is True and _hint(a[n], "read_only_hint") is False, n
    run_async(go())


def test_error_results_match_declared_schemas(tmp_path: Path):
    _fake_target(tmp_path, "tb9")
    _fake_target(tmp_path, "t0")

    # Every host file these cases use is in tmp_path; the default workspace is tested in test_default_workspace_confines_host_paths.
    ws = str(tmp_path)
    # A host binary that is not a QNX one: a small x86_64 Linux ELF file written inside the workspace,
    # so the case does not depend on the host's own /bin/ls (Mach-O on macOS).
    host_ls = write_elf(tmp_path / "ls")
    fixture = str(tmp_path / "fixture.txt")  # /etc/hostname does not exist on macOS
    Path(fixture).write_text("x\n")

    async def go():
        async with mcp_client({**_env(tmp_path), "QNX_MCP_WORKSPACE": ws}) as c:
            schemas = {t.name: t.output_schema for t in (await c.session.list_tools()).tools}
            cases = [
                ("target_status", {"name": "tb1"}, "target_not_found"),
                ("target_status", {"name": "t0"}, "target_not_allowed"),
                ("processes", {"target": "tb9"}, "target_down"),
                ("logs", {"target": "tb9"}, "target_down"),
                ("deploy_and_run", {"target": "tb9", "binary": host_ls}, "not_a_qnx_binary"),
                ("run_start", {"target": "tb9", "binary": host_ls}, "not_a_qnx_binary"),
                ("deploy_and_run", {"target": "tb9", "binary": "relative/path"}, "invalid_argument"),
                ("target_create", {"name": "tb9", "options": ["--ip=10.0.0.1"]}, "invalid_argument"),
                ("target_create", {"name": "tb5", "cpus": 9}, "invalid_argument"),  # rejected by the input schema itself
                ("get_crash", {"target": "tb9"}, "core_not_found"),
                ("get_crash", {"target": "tb9", "core_path": "/tmp/x.core"}, "invalid_argument"),
                ("run_poll", {"target": "tb9", "run_id": "r-000000000000"}, "invalid_argument"),
                ("run_stop", {"target": "tb9", "run_id": "nonsense"}, "invalid_argument"),
                ("file_put", {"target": "tb9", "local_path": fixture, "remote_path": "/etc/passwd"}, "path_not_allowed"),
                ("file_put", {"target": "tb9", "local_path": fixture, "remote_path": "/tmp/qnx-mcp/bin/x"}, "path_not_allowed"),
                ("file_get", {"target": "tb9", "remote_path": "/tmp/$(id)"}, "invalid_argument"),
                ("file_put", {"target": "tb9", "local_path": fixture, "remote_path": "/tmp//qnx-mcp/bin/x"}, "invalid_argument"),
                ("run_poll", {"target": "tb9", "run_id": "r-000000000000", "max_kb": 4096}, "invalid_argument"),
                ("target_destroy", {"name": "tb9", "confirm": "tb8"}, "invalid_argument"),
                ("target_destroy", {"name": "t0", "confirm": "t0"}, "target_not_allowed"),
                ("build", {"path": str(tmp_path / "nonexistent")}, "invalid_argument"),
                ("build", {"path": "/nonexistent/dir"}, "path_not_allowed"),
                ("build", {"path": str(tmp_path), "system": "make", "make_args": ["-f", "other.mk"]}, "invalid_argument"),
                ("build", {"path": str(tmp_path), "system": "qcc", "cflags": ["-wrapper,x"]}, "invalid_argument"),
                ("build", {"path": str(tmp_path), "system": "qcc", "ldflags": ["-L/usr/lib"]}, "path_not_allowed"),
                ("file_list", {"target": "tb9", "path": "/tmp/../etc"}, "invalid_argument"),
                ("file_list", {"target": "tb9", "path": "/tmp/"}, "invalid_argument"),
                ("file_list", {"target": "tb9", "path": "/tmp"}, "target_down"),
                ("file_list", {"target": "t0", "path": "/tmp"}, "target_not_allowed"),
                ("process_stop", {"target": "tb9", "pid": 1}, "process_not_allowed"),
                ("process_stop", {"target": "tb9", "pid": 4242}, "target_down"),
                ("process_stop", {"target": "tb9", "pid": 4242, "signal": "STOP"}, "invalid_argument"),
                ("deploy_and_run", {"target": "tb9", "remote_binary": "/tmp/qnx-mcp/bin/qnx-mcp-run-0"}, "path_not_allowed"),
                ("deploy_and_run", {"target": "tb9", "remote_binary": "/tmp/qnx-mcp"}, "path_not_allowed"),
                ("deploy_and_run", {"target": "tb9", "remote_binary": "/data/../tmp/qnx-mcp/bin/x"}, "invalid_argument"),
                ("deploy_and_run", {"target": "tb9", "binary": host_ls, "remote_binary": "/data/svc"}, "invalid_argument"),
                ("run_start", {"target": "tb9"}, "invalid_argument"),
                ("run_start", {"target": "tb9", "remote_binary": "/data/svc"}, "target_down"),
                ("deploy_and_run", {"target": "tb9", "remote_binary": "/proc/boot/ksh"}, "path_not_allowed"),
                ("run_start", {"target": "tb9", "remote_binary": "/data/home/sh"}, "path_not_allowed"),
                ("deploy_and_run", {"target": "tb9", "remote_binary": "/opt/tool"}, "path_not_allowed"),
                # SSH-only targets: no QNX_MCP_SSH_HOSTS is set here, so the server logs in to nothing
                ("target_register", {"name": "tb7", "host": "qnx.example.com", "arch": "aarch64le", "host_key_fingerprint": "SHA256:" + "A" * 43}, "ssh_host_not_allowed"),
                ("target_register", {"name": "tb7", "host": "169.254.1.1", "arch": "x86_64"}, "invalid_argument"),
                ("target_register", {"name": "tb7", "host": "qnx.example.com", "arch": "x86_64", "host_key_fingerprint": "SHA256:short"}, "invalid_argument"),
                ("target_register", {"name": "tb7", "host": "qnx.example.com", "arch": "arm"}, "invalid_argument"),  # the input schema itself
                ("target_register", {"name": "tb7", "host": "qnx.example.com", "arch": "x86_64", "port": 70000}, "invalid_argument"),
                ("target_register", {"name": "t0", "host": "qnx.example.com", "arch": "x86_64"}, "target_not_allowed"),
                ("target_unregister", {"name": "tb9", "confirm": "tb8"}, "invalid_argument"),
                ("target_unregister", {"name": "tb9", "confirm": "tb9"}, "target_kind_unsupported"),  # a QEMU target is destroyed, not unregistered
                ("target_unregister", {"name": "tb1", "confirm": "tb1"}, "target_not_found"),
                ("build", {"path": str(tmp_path), "target": "tb1"}, "target_not_found"),
                ("build", {"path": str(tmp_path), "target": "t0"}, "target_not_allowed"),
                ("build", {"path": str(tmp_path), "target": "tb9", "arch": "aarch64le"}, "arch_mismatch"),  # tb9 is x86_64
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


def test_default_workspace_confines_host_paths(tmp_path: Path):
    """QNX_MCP_WORKSPACE unset or empty: host paths must be inside the server's working directory."""
    _fake_target(tmp_path, "tb9")
    cwd = os.path.realpath(os.getcwd())
    assert cwd != "/"

    async def go():
        async with mcp_client({**_env(tmp_path), "QNX_MCP_WORKSPACE": ""}) as c:
            for tool, args in (("file_put", {"target": "tb9", "local_path": "/etc/hosts", "remote_path": "/tmp/x"}),
                               ("file_get", {"target": "tb9", "remote_path": "/tmp/x", "local_path": str(tmp_path / "x")}),
                               ("deploy_and_run", {"target": "tb9", "binary": "/bin/ls"}),
                               ("get_crash", {"target": "tb9", "core_path": "/tmp/x.core", "binary": "/bin/ls"}),
                               ("build", {"path": str(tmp_path)})):
                err, data = await c.call(tool, **args)
                assert err and data["error"]["code"] == "path_not_allowed", (tool, data)
            err, data = await c.call("environment_check")
            ws = {ch["check"]: ch for ch in data["checks"]}["workspace"]
            assert ws["ok"] and cwd in ws["detail"] and "not set" in ws["detail"], ws
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
