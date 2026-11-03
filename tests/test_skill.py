# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""The agent skill (skills/qnx-mcp/SKILL.md) must only name tools and parameters the server advertises, and name every tool."""

from __future__ import annotations

import re
from pathlib import Path

from helpers import mcp_client, run_async

SKILL = Path(__file__).resolve().parent.parent / "skills" / "qnx-mcp" / "SKILL.md"


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def _calls(text: str) -> list[tuple[str, list[str]]]:
    """Every `name(...)` code span and every mcp__qnx__name, with the parameter names used in the call."""
    calls = []
    for span in re.findall(r"`([^`\n]+)`", text):
        for m in re.finditer(r"\b([a-z][a-z0-9_]*)\(([^()]*)\)", span):
            params, depth, cur = [], 0, ""
            for ch in m.group(2) + ",":
                if ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                if ch == "," and depth == 0:
                    if cur.strip():
                        params.append(cur.split("=", 1)[0].strip())
                    cur = ""
                else:
                    cur += ch
            calls.append((m.group(1), params))
    calls += [(name, []) for name in re.findall(r"mcp__qnx__([a-z_]+)", text)]
    return calls


def test_skill_frontmatter():
    fm = _frontmatter(SKILL.read_text())
    assert fm["name"] == SKILL.parent.name == "qnx-mcp"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"])
    assert 0 < len(fm["description"]) <= 1024


def test_skill_names_only_real_tools_and_parameters(tmp_path: Path):
    env = {"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "state"), "QNX_MCP_ALLOW_TARGETS": "cl*"}

    async def go():
        async with mcp_client(env) as c:
            return {t.name: set((t.input_schema or {}).get("properties", {})) for t in (await c.session.list_tools()).tools}

    tools = run_async(go())
    calls = _calls(SKILL.read_text())
    assert calls
    for name, params in calls:
        assert name in tools, f"SKILL.md names {name}, which the server does not have"
        unknown = [p for p in params if p not in tools[name]]
        assert not unknown, f"SKILL.md calls {name} with {unknown}; its parameters are {sorted(tools[name])}"
    missing = set(tools) - {name for name, _ in calls}
    assert not missing, f"SKILL.md does not mention {sorted(missing)}"
