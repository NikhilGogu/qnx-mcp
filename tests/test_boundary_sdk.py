# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""The build boundary against the real QNX SDP: the allow-list accepts its documented forms with real qcc,
refuses the rest before qcc starts, and checks linker inputs and host paths. The last test needs a target."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import mcp_client, run_async
from qnx_mcp.sdk import build, sdk_env, sdk_tool
from qnx_mcp.util import Config, QnxMcpError

CFG = Config.from_env()
pytestmark = pytest.mark.skipif(not CFG.sdp_env.is_file(), reason="QNX SDP not installed")
TARGET = os.environ.get("QNX_MCP_E2E_TARGET")


def _cfg(ws: Path) -> Config:
    return Config.from_env({**os.environ, "QNX_MCP_WORKSPACE": str(ws)})


def _qcc(*args: str, cwd: Path) -> None:
    env = sdk_env(CFG)
    r = subprocess.run([sdk_tool(env, "qcc"), "-Vgcc_ntox86_64", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def _project(tmp_path: Path) -> Path:
    """main.c uses a shared library and an archive in lib/, an object file and two headers."""
    ws = tmp_path / "ws"
    proj = ws / "proj"
    (proj / "include").mkdir(parents=True)
    (proj / "lib").mkdir()
    (proj / "extra").mkdir()
    (proj / "include" / "cfg.h").write_text("#define CFG_ZERO 0\n")
    (proj / "pre.h").write_text("#define PRE_ONE 1\n")
    (proj / "main.c").write_text('#include "cfg.h"\nint lib_value(void);\nint obj_value(void);\nint arch_value(void);\n'
                                 "int main(void) { return lib_value() + obj_value() + arch_value() - 6 + CFG_ZERO + PRE_ONE - 1; }\n")
    (proj / "extra" / "lib.c").write_text("int lib_value(void) { return 1; }\n")
    (proj / "extra" / "obj.c").write_text("int obj_value(void) { return 2; }\n")
    (proj / "extra" / "arch.c").write_text("int arch_value(void) { return 3; }\n")
    _qcc("-fPIC", "-shared", "-o", "lib/libhelper.so", "extra/lib.c", cwd=proj)
    _qcc("-c", "-o", "obj.o", "extra/obj.c", cwd=proj)
    _qcc("-c", "-o", "extra/arch.o", "extra/arch.c", cwd=proj)
    env = sdk_env(CFG)
    ar = shutil.which("ntox86_64-ar", path=env["PATH"])
    assert ar, "ntox86_64-ar not in the SDP"
    subprocess.run([ar, "rcs", "lib/libarch.a", "extra/arch.o"], cwd=proj, env=env, check=True, timeout=60)
    return proj


def test_documented_safe_forms_build_with_real_qcc(tmp_path: Path):
    proj = _project(tmp_path)
    cfg = _cfg(proj.parent)
    cflags = ["-g", "-O2", "-Wall", "-Wextra", "-Werror=format", "-Wno-unused-parameter", "-DFEATURE=1", '-DVERSION="1.2"',
              "-D", "OTHER=2", "-UNDEBUG", "-U", "NDEBUG2", "-std=gnu11", "-fno-inline", "-fstack-protector-strong", "-march=x86-64",
              "-pthread", "-Iinclude", "-I", str(proj / "include"), "-isystem", "include", "-iquote", "include", "-include", "pre.h",
              f"-fdebug-prefix-map={proj}=/src"]
    ldflags = ["-Wl,--build-id=sha1", "-Wl,-z,relro,-z,now", "-Wl,--gc-sections", "-Wl,-Map=out.map", "obj.o",
               "-Llib", "-L", str(proj / "lib"), "-lhelper", "-larch", "-lm"]
    r = build(cfg, str(proj), system="qcc", sources=["main.c"], output=str(proj / "app"), cflags=cflags, ldflags=ldflags)
    assert r["ok"], r["steps"]
    art = r["artifacts"][0]
    assert art["qnx"] is True and art["arch"] == "x86_64" and art["build_id"], art
    assert (proj / "out.map").is_file()


# Each is refused by the server before qcc runs (the output file is never written).
REFUSED = [
    ("cflags", ["-wrapper", "/bin/true"]),
    ("cflags", ["-specs=x.specs"]),
    ("cflags", ["-fplugin=./x.so"]),
    ("cflags", ["-B/tmp"]),
    ("cflags", ["@args.txt"]),
    ("cflags", ["-Wa,-o,x"]),
    ("cflags", ["-Wp,-MD,x"]),
    ("cflags", ["-Xlinker", "-T"]),
    ("cflags", ["-I/etc"]),
    ("cflags", ["-I", "../.."]),
    ("cflags", ["-include", "/etc/hostname"]),
    ("cflags", ["--sysroot=/"]),
    ("cflags", ["-DX=$(id)"]),
    ("cflags", ['-DX="a b"']),
    ("cflags", ["-fuse-ld=bfd"]),
    ("cflags", ["-o/tmp/x"]),
    ("cflags", ["-save-temps=obj"]),
    ("ldflags", ["-Wl,-T,x.ld"]),
    ("ldflags", ["-Wl,--wrap=open"]),
    ("ldflags", ["-Wl,-rpath,/data/lib"]),
    ("ldflags", ["-Wl,-Map=/tmp/out.map"]),
    ("ldflags", ["-L/usr/lib"]),
    ("ldflags", ["-l../x"]),
    ("sources", ["notes.txt"]),
]


@pytest.mark.parametrize("field,args", REFUSED, ids=[f"{f}:{' '.join(a)}" for f, a in REFUSED])
def test_unsafe_forms_are_refused_before_qcc(tmp_path: Path, field: str, args: list[str]):
    proj = tmp_path / "ws" / "proj"
    proj.mkdir(parents=True)
    (proj / "main.c").write_text("int main(void) { return 0; }\n")
    (proj / "notes.txt").write_text("text\n")
    out = proj / "app"
    with pytest.raises(QnxMcpError) as e:
        build(_cfg(proj.parent), str(proj), system="qcc", output=str(out), **{field: args})
    assert e.value.code in ("invalid_argument", "path_not_allowed") and field in e.value.message, e.value.to_dict()
    assert not out.exists()


def test_flags_outside_qcc(tmp_path: Path):
    """make mode takes no cflags or ldflags; make_args take only listed forms; allowed CFLAGS reach the real build."""
    proj = tmp_path / "ws" / "proj"
    proj.mkdir(parents=True)
    (proj / "main.c").write_text("int main(void) { return EXTRA; }\n")
    (proj / "Makefile").write_text("app: main.c\n\t$(CC) $(CFLAGS) -o app main.c\n")
    cfg = _cfg(proj.parent)
    for kw in ({"cflags": ["-O2"]}, {"ldflags": ["-lm"]}, {"make_args": ["SHELL=/bin/sh"]}, {"make_args": ["-f", "other.mk"]},
               {"make_args": ["CC=/bin/true"]}, {"make_args": ["CFLAGS=-wrapper,/bin/true"]}, {"make_args": ["--eval=x"]}):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(proj), system="make", **kw)
        assert e.value.code == "invalid_argument", e.value.to_dict()
    assert not (proj / "app").exists()
    r = build(cfg, str(proj), system="make", make_args=["app", "CFLAGS=-g -DEXTRA=0"])
    assert r["ok"] and r["artifacts"] and r["artifacts"][0]["qnx"] is True, r["steps"]


def test_linker_inputs_are_checked_by_content(tmp_path: Path):
    """A text file named like an object or library is a linker script to ld, also when -l finds it in a -L directory."""
    proj = _project(tmp_path)
    cfg = _cfg(proj.parent)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.c").write_text("int lib_value(void) { return 1; }\n")
    _qcc("-c", "-o", "o.o", "o.c", cwd=outside)
    (proj / "fake").mkdir()
    script = f"INPUT({outside / 'o.o'})\n"
    for name in ("x.o", "libx.a", "fake/libtext.so", "fake/libtext.a"):
        (proj / name).write_text(script)
    (proj / "fake" / "libthin.a").write_bytes(b"!<thin>\n")
    (proj / "fake" / "liblink.so").symlink_to(outside / "o.o")
    out = proj / "app"
    for ld in (["x.o"], ["libx.a"], ["-Lfake", "-ltext"], ["-L", "fake", "-ltext"], ["-L", str(proj / "fake"), "-lthin"], ["-Lfake", "-llink"]):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(proj), system="qcc", sources=["main.c"], output=str(out), ldflags=ld)
        assert e.value.code in ("invalid_argument", "path_not_allowed") and "ldflags" in e.value.message, (ld, e.value.to_dict())
    with pytest.raises(QnxMcpError):  # -l in cflags and -L in ldflags reach the same link
        build(cfg, str(proj), system="qcc", sources=["main.c"], output=str(out), cflags=["-ltext"], ldflags=["-Lfake"])
    assert not out.exists()
    r = build(cfg, str(proj), system="qcc", sources=["main.c"], output=str(out), cflags=["-include", "pre.h", "-Iinclude"],
              ldflags=["obj.o", "-Llib", "-lhelper", "-larch"])
    assert r["ok"] and out.is_file(), r["steps"]


def test_host_paths_stay_inside_the_workspace(tmp_path: Path):
    proj = tmp_path / "ws" / "proj"
    proj.mkdir(parents=True)
    (proj / "main.c").write_text("int main(void) { return 0; }\n")
    other = tmp_path / "other"
    other.mkdir()
    (other / "main.c").write_text("int main(void) { return 0; }\n")
    (proj / "out").symlink_to(other / "app")
    (proj / "linked").symlink_to(other)
    cfg = _cfg(proj.parent)
    for kw in ({"path": str(other)}, {"path": str(proj / "linked")}, {"path": str(proj), "output": str(proj / "out")},
               {"path": str(proj), "output": str(other / "app")}, {"path": str(proj), "sources": [str(other / "main.c")]}):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, **{"system": "qcc", **kw})
        assert e.value.code == "path_not_allowed", (kw, e.value.to_dict())
    assert not (other / "app").exists()


def test_cmake_toolchain_file_is_not_followed(tmp_path: Path):
    """A link planted at the toolchain file's name is replaced, never written through (with or without cmake on the host)."""
    proj = tmp_path / "ws" / "proj"
    bdir = proj / "build-qnx-x86_64"
    bdir.mkdir(parents=True)
    (proj / "main.c").write_text("int main(void) { return 0; }\n")
    (proj / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\nproject(x C)\nadd_executable(x main.c)\n")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep me\n")
    (bdir / "qnx-toolchain.cmake").symlink_to(outside)
    try:
        build(_cfg(proj.parent), str(proj), system="cmake")
    except QnxMcpError as e:
        assert e.code == "tool_missing" and shutil.which("cmake") is None, e.to_dict()
    tc = bdir / "qnx-toolchain.cmake"
    assert outside.read_bytes() == b"keep me\n"
    assert not tc.is_symlink() and "CMAKE_SYSTEM_NAME QNX" in tc.read_text()


@pytest.mark.skipif(not TARGET, reason="set QNX_MCP_E2E_TARGET to a running target")
def test_server_refuses_unsafe_state_dir_key_and_args(tmp_path: Path):
    """Through the real server: a state dir or key path ssh would expand, and run arguments with a NUL."""
    state = os.environ.get("QNX_MCP_STATE_DIR", str(Path.home() / ".qnx-mcp" / "state"))

    async def go():
        async with mcp_client({"QNX_MCP_STATE_DIR": str(tmp_path / "state dir")}) as c:
            err, st = await c.call("target_status", name=TARGET)  # refused like every other tool, not ok:true
            assert err and st["error"]["code"] == "internal_error" and "QNX_MCP_STATE_DIR" in st["error"]["hint"], st
            err, ps = await c.call("processes", target=TARGET)
            assert err and ps["error"]["code"] == "internal_error" and "QNX_MCP_STATE_DIR" in ps["error"]["hint"], ps
        async with mcp_client({"QNX_MCP_STATE_DIR": state, "QNX_MCP_SSH_KEY": str(tmp_path / "key%h")}) as c:
            err, st = await c.call("target_status", name=TARGET)
            assert err and st["error"]["code"] == "internal_error" and "QNX_MCP_SSH_KEY" in st["error"]["hint"], st
            err, ps = await c.call("processes", target=TARGET)
            assert err and ps["error"]["code"] == "internal_error" and "QNX_MCP_SSH_KEY" in ps["error"]["hint"], ps
            err, env = await c.call("environment_check")
            assert not {x["check"]: x for x in env["checks"]}["ssh_key"]["ok"], env
        async with mcp_client({}) as c:
            err, r = await c.call("deploy_and_run", target=TARGET, remote_binary="/data/none", args=["a\0b"])
            assert err and r["error"]["code"] == "invalid_argument" and "args[0]" in r["error"]["message"], r
    run_async(go())
