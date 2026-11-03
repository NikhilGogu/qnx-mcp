# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Drive the qnx-mcp server exactly as an agent would: MCP ClientSession over stdio."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PASS_ENV = ("HOME", "PATH", "USER", "LANG", "QNX_MCP_HOME", "QNX_MCP_SDP_ENV", "QNX_MCP_TARGETS_DIR", "QNX_MCP_STATE_DIR",
            "QNX_MCP_ALLOW_TARGETS", "QNX_MCP_SSH_KEY", "QNX_MCP_DHCP_LEASES")


class Client:
    def __init__(self, session: ClientSession, log: list[str]):
        self.session = session
        self.log = log

    async def call(self, tool: str, **args: Any) -> tuple[bool, dict[str, Any]]:
        t0 = time.monotonic()
        r = await self.session.call_tool(tool, args, read_timeout_seconds=900)
        dt = time.monotonic() - t0
        data = r.structured_content or {}
        self.log.append(f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())}) -> is_error={r.is_error} in {dt:.2f}s")
        return bool(r.is_error), data


@asynccontextmanager
async def mcp_client(env: dict[str, str] | None = None, log: list[str] | None = None) -> AsyncIterator[Client]:
    full = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    full.update(env or {})
    params = StdioServerParameters(command=sys.executable, args=["-m", "qnx_mcp.server"], env=full)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            yield Client(session, log if log is not None else [])


def run_async(coro):
    return asyncio.run(coro)
