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
            "QNX_MCP_ALLOW_TARGETS", "QNX_MCP_SSH_KEY", "QNX_MCP_DHCP_LEASES", "QNX_MCP_WORKSPACE",
            "QNX_MCP_RUN_AS")  # set it to run the whole live suite with programs as that user


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


def write_elf(path: Any, *, machine: int = 62, interpreter: str = "/lib64/ld-linux-x86-64.so.2") -> str:
    """A minimal 64-bit little-endian ELF executable header with one PT_INTERP segment and no sections.
    Default: an x86_64 Linux program, so the tests do not depend on the host's own /bin/ls being ELF."""
    import struct

    interp = interpreter.encode() + b"\0"
    phoff, phentsize = 64, 56
    interp_off = phoff + phentsize
    ident = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9)
    header = ident + struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0, phoff, 0, 0, 64, phentsize, 1, 64, 0, 0)
    phdr = struct.pack("<IIQQQQQQ", 3, 4, interp_off, 0, 0, len(interp), len(interp), 1)
    with open(path, "wb") as f:
        f.write(header + phdr + interp)
    os.chmod(path, 0o755)
    return str(path)
