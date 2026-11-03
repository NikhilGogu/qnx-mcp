# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""build tool against the real QNX SDP: cmake, make and qcc projects (no target needed)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from qnx_mcp.sdk import build
from qnx_mcp.util import Config

CFG = Config.from_env()
pytestmark = pytest.mark.skipif(not CFG.sdp_env.is_file(), reason="QNX SDP not installed")
SRC = '#include <stdio.h>\nint main(void)\n{\n    int unused;\n    puts("hello from QNX");\n    return 0;\n}\n'


def test_cmake_project(tmp_path: Path):
    (tmp_path / "main.c").write_text(SRC)
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\nproject(hello C)\nadd_executable(hello main.c)\ntarget_compile_options(hello PRIVATE -Wall)\n")
    r = build(CFG, str(tmp_path))
    assert r["ok"] and r["system"] == "cmake", r["steps"]
    exe = [a for a in r["artifacts"] if a["path"].endswith("/hello")]
    assert exe and exe[0]["qnx"] is True and exe[0]["arch"] == "x86_64" and exe[0]["debug_info"] is True, r["artifacts"]
    w = [d for d in r["diagnostics"] if d["severity"] == "warning"]
    assert w and w[0]["line"] == 4 and w[0]["option"] == "-Wunused-variable" and w[0]["source"] == "gcc-text", r["diagnostics"]
    print("\ncmake:", r["steps"][0]["argv"][:4], "->", exe[0]["path"], "| warning:", w[0]["file"], w[0]["line"], w[0]["column"], w[0]["message"])


def test_make_project(tmp_path: Path):
    (tmp_path / "main.c").write_text(SRC.replace("int unused;", "undeclared = 1;"))
    (tmp_path / "Makefile").write_text("hello: main.c\n\t$(CC) -g -Wall -o $@ $<\n")
    r = build(CFG, str(tmp_path))
    assert not r["ok"] and r["system"] == "make" and r["error"]["code"] == "build_failed"
    e = [d for d in r["diagnostics"] if d["severity"] == "error" and d["file"]]
    assert e and e[0]["file"] == str(tmp_path / "main.c") and e[0]["line"] == 4 and e[0]["function"] == "main", r["diagnostics"]
    (tmp_path / "main.c").write_text(SRC)
    r = build(CFG, str(tmp_path))
    assert r["ok"] and any(a["path"].endswith("/hello") and a["qnx"] for a in r["artifacts"]), r
    print("\nmake: error diag", {k: e[0][k] for k in ("file", "line", "column", "severity", "message", "function")})


def test_aarch64_cross_build(tmp_path: Path):
    (tmp_path / "main.c").write_text(SRC)
    r = build(CFG, str(tmp_path / "main.c"), arch="aarch64le", cflags=["-g", "-O2"])
    assert r["ok"] and r["artifacts"][0]["arch"] == "aarch64le" and r["artifacts"][0]["qnx"] is True, r
