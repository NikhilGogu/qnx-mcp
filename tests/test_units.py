# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Offline unit tests: parsers, validation, quoting, timeouts and locking. No QNX target needed."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import struct

from qnx_mcp.crash import _source_context
from qnx_mcp.ops import _install, _write_root
from qnx_mcp.parsers import core_read, elf_info, gnu_build_id, parse_diagnostics, parse_mi_output, parse_pidin, parse_slog2
from qnx_mcp.target import Target, list_targets, parse_leases, validate_mkqnximage_options
from qnx_mcp.util import (
    CURRENT_CALL, CallContext, Config, QnxMcpError, check_host_path, check_remote_path, check_target_name, file_lock, private_dir,
    quote_argv, run,
)

GCC_JSON = ('[{"kind": "error", "column-origin": 1, "children": [{"kind": "note", "locations": [{"caret": {"line": 1, "file": "bad.c", '
            '"column": 22}}], "message": "each undeclared identifier is reported only once"}], "locations": [{"caret": {"line": 1, '
            '"file": "bad.c", "column": 22}}], "message": "\'y\' undeclared (first use in this function)"}, {"kind": "warning", '
            '"locations": [{"caret": {"line": 2, "file": "bad.c", "column": 18}}], "option": "-Wunused-variable", "message": "unused variable \'z\'"}]')


def test_gcc_json_diagnostics():
    d = parse_diagnostics(GCC_JSON, "/src")
    assert [x["severity"] for x in d] == ["error", "warning"]
    assert d[0] | {} == d[0] and d[0]["file"] == "/src/bad.c" and d[0]["line"] == 1 and d[0]["column"] == 22
    assert d[0]["notes"][0]["message"].startswith("each undeclared")
    assert d[1]["option"] == "-Wunused-variable"


def test_text_and_linker_diagnostics():
    out = "\n".join([
        "make: Entering directory '/w'",
        "main.c: In function 'main':",
        "main.c:7:5: warning: implicit declaration of function 'foo' [-Wimplicit-function-declaration]",
        "main.c:9:12: error: expected ';' before '}' token",
        "/opt/qnx/ld: main.o: in function `main':",
        "main.c:(.text+0x1a): undefined reference to `foo'",
        "collect2: error: ld returned 1 exit status",
    ])
    d = parse_diagnostics(out, "/w")
    assert d[0] == {"file": "/w/main.c", "line": 7, "column": 5, "severity": "warning", "message": "implicit declaration of function 'foo'",
                    "option": "-Wimplicit-function-declaration", "source": "gcc-text", "function": "main"}
    assert d[1]["severity"] == "error" and d[1]["line"] == 9
    assert any(x["source"] == "linker" and "undefined reference" in x["message"] for x in d)
    assert any(x["source"] == "toolchain" and "ld returned 1" in x["message"] for x in d)


MI = r'''=thread-group-added,id="i1"
~"Reading symbols from crashx...\n"
~"Program terminated with signal SIGSEGV, Segmentation fault.\n"
(gdb)
1^done,threads=[{id="1",target-id="pid 798741 tid 1",frame={level="0",addr="0x00000039c811a75b",func="main",args=[],file="c.c",fullname="/w/c.c",line="3",arch="i386:x86-64"},state="stopped"}],current-thread-id="1"
4^done,value="0x0"
5^done
6^done,stack=[frame={level="0",addr="0x1",func="leaf",file="c.c",fullname="/w/c.c",line="3"},frame={level="1",addr="0x2",func="main",file="c.c",fullname="/w/c.c",line="9"}]
7^error,msg="No symbol \"x\" in current context."
~"=> 0x39c811a75b <main+26>:\tmov    (%rax),%eax\n"
'''


def test_mi_parser():
    mi = parse_mi_output(MI)
    res = mi["results"]
    assert res["1"]["threads"][0]["frame"]["func"] == "main"
    assert res["4"]["value"] == "0x0"
    frames = [next(iter(f.values())) for f in res["6"]["stack"]]
    assert [f["func"] for f in frames] == ["leaf", "main"] and frames[1]["line"] == "9"
    assert res["7"]["class"] == "error" and 'No symbol "x"' in res["7"]["msg"]
    assert any("SIGSEGV" in c for c in mi["console"])
    assert any(c.startswith("=> 0x39c811a75b") for c in mi["console"])


def test_pidin_parser():
    procs = ("     pid|   ppid|       uid|        code| data|name              |name     |Arguments\n"
             "       1|      1|         0|        788K| 157K|procnto-smp-instr |/proc/boot/procnto-smp-instr|procnto-smp-instr -mr -d 0777 \n"
             "  159756|      1|        30|         40K| 1.5M|dumper            |/proc/boot/dumper|dumper -U 30:30 -d /data/var/dumper | tee x\n")
    threads = ("     pid|tid|prio|STATE         |Blocked\n"
               "       1|  1|  0f|READY         |\n"
               "       1|  3|255i|INTR          |\n"
               "  159756|  1| 10r|RECEIVE       |1\n")
    p = parse_pidin(procs, threads)
    assert [x["pid"] for x in p] == [1, 159756]
    assert p[1]["uid"] == 30 and p[1]["data_bytes"] == int(1.5 * 1024 * 1024) and p[1]["args"].endswith("| tee x")
    assert p[0]["threads"][1] == {"tid": 3, "priority": 255, "policy": "i", "state": "INTR", "blocked_on": None}
    assert p[1]["threads"][0]["policy"] == "RR" and p[1]["threads"][0]["blocked_on"] == "1"


def test_slog2_parser():
    text = ("1793718942.518              io_usb_otg.282643           io-usb-otg    8     12  4  usbdi disconnect\n"
            "1793718943.260         sshd_session.835586..1                 slog*   1      0  5  <38>Accepted publickey for root\n"
            "    continuation line\n")
    e = parse_slog2(text)
    assert e[0]["process"] == "io_usb_otg" and e[0]["pid"] == 282643 and e[0]["severity_name"] == "notice"
    assert e[1]["process"] == "sshd_session" and e[1]["pid"] == 835586 and e[1]["buffer"] == "slog"
    assert e[1]["message"].endswith("continuation line")


def test_quote_argv_blocks_injection():
    evil = ["echo", "a; rm -rf /", "$(reboot)", "`id`", "it's"]
    q = quote_argv(evil)
    assert shlex.split(q) == evil


@pytest.mark.parametrize("name", ["T0", "../x", "a b", "", "x" * 40, "tb;id"])
def test_bad_target_names(name):
    with pytest.raises(QnxMcpError) as e:
        check_target_name(Config.from_env({"HOME": "/tmp"}), name)
    assert e.value.code == "invalid_argument"


def test_allowlist():
    cfg = Config.from_env({"HOME": "/tmp", "QNX_MCP_ALLOW_TARGETS": "tb,tb[0-9]*"})
    assert check_target_name(cfg, "tb2") == "tb2"
    with pytest.raises(QnxMcpError) as e:
        check_target_name(cfg, "t0")
    assert e.value.code == "target_not_allowed"


@pytest.mark.parametrize("path", ["relative", "/tmp/a b", "/tmp/$(id)", "/tmp/../etc/passwd", "/tmp/x;y", "/tmp/'q'"])
def test_bad_remote_paths(path):
    with pytest.raises(QnxMcpError):
        check_remote_path(path)


def test_mkqnximage_option_allowlist():
    assert validate_mkqnximage_options(["--hostname=dev1", "--data-size=512"]) == ["--hostname=dev1", "--data-size=512"]
    for bad in ["--hostname=a'b", "--ip=1.2.3.4", "--policy=/etc/x", "--data-size=1;id", "--run"]:
        with pytest.raises(QnxMcpError):
            validate_mkqnximage_options([bad])


def test_run_timeout_kills_process_group(tmp_path: Path):
    marker = tmp_path / "survived"
    t0 = time.monotonic()
    r = run(["bash", "-c", f"(sleep 3; touch {marker}) & sleep 30"], timeout=0.5)
    assert r.timed_out and r.returncode is None and time.monotonic() - t0 < 5
    time.sleep(3.5)
    assert not marker.exists(), "background grandchild outlived the timeout"


def test_lock_contention(tmp_path: Path):
    lock = tmp_path / "lock"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with file_lock(lock, exclusive=True, timeout=1, what="t"):
            held.set()
            release.wait(5)

    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    with pytest.raises(QnxMcpError) as e:
        with file_lock(lock, exclusive=False, timeout=0.3, what="target tb"):
            pass
    assert e.value.code == "target_busy"
    release.set()
    th.join()
    with file_lock(lock, exclusive=False, timeout=0.3, what="t"):
        pass


def test_elf_info_on_host_binary(tmp_path: Path):
    from helpers import write_elf
    info = elf_info(write_elf(tmp_path / "linux-prog"))
    assert info and info["arch"] == "x86_64" and info["qnx"] is False and info["type"] == "executable"
    info = elf_info(write_elf(tmp_path / "qnx-prog", machine=183, interpreter="/usr/lib/ldqnx-64.so.2"))
    assert info and info["arch"] == "aarch64le" and info["qnx"] is True
    host = elf_info("/bin/ls")  # an ELF file on Linux hosts, Mach-O on macOS
    if host is not None:
        assert host["qnx"] is False
    assert elf_info(__file__) is None


def test_allowlist_fails_closed():
    for env in ({"HOME": "/tmp"}, {"HOME": "/tmp", "QNX_MCP_ALLOW_TARGETS": " , "}):
        with pytest.raises(QnxMcpError) as e:
            check_target_name(Config.from_env(env), "tb")
        assert e.value.code == "target_not_allowed" and "QNX_MCP_ALLOW_TARGETS" in e.value.hint
    assert check_target_name(Config.from_env({"HOME": "/tmp", "QNX_MCP_ALLOW_TARGETS": "*"}), "anything") == "anything"


def test_fullmatch_rejects_trailing_newline():
    cfg = Config.from_env({"HOME": "/tmp", "QNX_MCP_ALLOW_TARGETS": "*"})
    for bad in ("tb\n", "tb2\n"):
        with pytest.raises(QnxMcpError):
            check_target_name(cfg, bad)
    for bad in ("/tmp/x\n", "/tmp/./x", "/tmp/x/.."):
        with pytest.raises(QnxMcpError):
            check_remote_path(bad)
    with pytest.raises(QnxMcpError):
        validate_mkqnximage_options(["--hostname=dev1\n"])


def test_leases_ignore_expired_and_prefer_newest():
    now = 1_000_000
    mac = "52:54:00:aa:bb:cc"
    leases = json.dumps([
        {"ip-address": "192.168.122.90", "mac-address": mac, "expiry-time": now - 10},
        {"ip-address": "192.168.122.91", "mac-address": mac.upper(), "expiry-time": now + 100},
        {"ip-address": "192.168.122.92", "mac-address": mac, "expiry-time": now + 500},
        {"ip-address": "not-an-ip", "mac-address": mac, "expiry-time": now + 900},
        {"ip-address": "192.168.122.93", "mac-address": "52:54:00:00:00:01", "expiry-time": now + 900},
        {"ip-address": "192.168.122.94", "mac-address": mac, "expiry-time": 0},
    ])
    assert parse_leases(leases, mac, now) == ["192.168.122.94", "192.168.122.92", "192.168.122.91"]
    assert parse_leases("garbage", mac, now) == []


def test_mi_parser_flags_malformed_untokened_and_duplicates():
    mi = parse_mi_output('6^done,stack=[frame={level="0"\n^done\n7^done,a="1"\n7^done,a="2"\n&"warning: x\\n"\nnoise\n')
    assert mi["results"]["6"]["malformed"] is True
    assert mi["untokened"] == [{"class": "done"}] and "1" not in mi["results"]
    assert mi["duplicates"] == ["7"] and mi["results"]["7"]["a"] == "1"
    assert mi["log"] == ["warning: x\n"] and mi["unparsed"] == ["noise"]


def test_run_bounds_output():
    r = run(["bash", "-c", "head -c 3000000 /dev/zero; echo err >&2"], timeout=20, max_output=1 << 20)
    assert len(r.stdout) == 1 << 20 and r.stdout_total == 3000000 and r.truncated and r.err == "err\n"


def test_cancel_kills_running_process():
    ctx = CallContext()
    out: dict = {}

    def work():
        tok = CURRENT_CALL.set(ctx)
        try:
            run(["sleep", "30"], timeout=60)
        except QnxMcpError as e:
            out["code"] = e.code
        finally:
            CURRENT_CALL.reset(tok)

    th = threading.Thread(target=work)
    t0 = time.monotonic()
    th.start()
    time.sleep(0.5)
    ctx.cancel()
    th.join(10)
    assert out.get("code") == "cancelled" and time.monotonic() - t0 < 5


def test_private_dir_mode_and_symlink_refusal(tmp_path: Path):
    d = private_dir(tmp_path / "a" / "b")
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700 and stat.S_IMODE(os.stat(tmp_path / "a").st_mode) == 0o700
    (tmp_path / "link").symlink_to(tmp_path / "a")
    with pytest.raises(QnxMcpError):
        private_dir(tmp_path / "link")


def test_symlinked_target_is_refused(tmp_path: Path):
    other = tmp_path / "elsewhere" / "t0"
    (other / "local").mkdir(parents=True)
    (other / "local" / "options").write_text("OPT_ARCH='x86_64'\n")
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "tb3").symlink_to(other)
    cfg = Config.from_env({"HOME": str(tmp_path), "QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "state"),
                           "QNX_MCP_ALLOW_TARGETS": "tb*"})
    with pytest.raises(QnxMcpError) as e:
        Target(cfg, "tb3")
    assert e.value.code == "target_not_allowed"
    assert list_targets(cfg) == []


def test_workspace_confines_host_paths(tmp_path: Path):
    ws = (tmp_path / "ws").resolve()
    ws.mkdir()
    (ws / "a.c").write_text("x\n")
    (tmp_path / "secret.c").write_text("s\n")
    (ws / "link.c").symlink_to(tmp_path / "secret.c")
    (ws / "notes.txt").write_text("t\n")
    cfg = Config.from_env({"HOME": str(tmp_path), "QNX_MCP_WORKSPACE": str(ws), "QNX_MCP_ALLOW_TARGETS": "*"})
    assert check_host_path(cfg, str(ws / "a.c"), "f") == ws / "a.c"
    for p in (tmp_path / "secret.c", ws / "link.c"):
        with pytest.raises(QnxMcpError) as e:
            check_host_path(cfg, str(p), "f")
        assert e.value.code == "path_not_allowed"
    assert _source_context(cfg, str(ws / "a.c"), 1)[0]["text"] == "x"
    assert _source_context(cfg, str(ws / "link.c"), 1) is None  # DWARF path pointing outside the workspace
    assert _source_context(cfg, str(ws / "notes.txt"), 1) is None  # not a C/C++ source


@pytest.mark.parametrize("path", ["/tmp//qnx-mcp/bin/x", "/tmp/./qnx-mcp/x", "/tmp/x/", "//tmp/x", "/tmp/a//b"])
def test_remote_paths_must_be_normalised(path):
    with pytest.raises(QnxMcpError) as e:
        check_remote_path(path)
    assert e.value.code == "invalid_argument"


def test_write_root_refuses_managed_dir():
    assert _write_root("/tmp/app/config.json") == ("/tmp", "app/config.json")
    assert _write_root("/data/home/x") == ("/data", "home/x")
    for bad in ("/tmp/qnx-mcp/bin/x", "/tmp/qnx-mcp", "/etc/passwd", "/tmp", "/datax/y"):
        with pytest.raises(QnxMcpError) as e:
            _write_root(bad)
        assert e.value.code == "path_not_allowed", bad


def test_run_keeps_output_when_a_descendant_holds_the_pipe(tmp_path: Path):
    marker = tmp_path / "late"
    t0 = time.monotonic()
    r = run(["bash", "-c", f"echo hi; (sleep 3; touch {marker}) & exit 0"], timeout=30)
    assert r.out == "hi\n" and r.returncode == 0 and time.monotonic() - t0 < 6, (r.out, time.monotonic() - t0)
    time.sleep(3.5)
    assert not marker.exists(), "descendant holding the pipe outlived run()"


def test_run_streams_with_exact_cap(tmp_path: Path):
    with open(tmp_path / "out", "wb") as f:
        r = run(["head", "-c", "3000000", "/dev/zero"], timeout=20, max_output=1_000_000, stdout_file=f)
    assert (tmp_path / "out").stat().st_size == 1_000_000 and r.streamed_over(1_000_000)
    with open(tmp_path / "small", "wb") as f:
        r = run(["head", "-c", "1000", "/dev/zero"], timeout=20, max_output=1000, stdout_file=f)
    assert (tmp_path / "small").stat().st_size == 1000 and not r.streamed_over(1000)


def test_first_use_trust_is_off_by_default(tmp_path: Path):
    (tmp_path / "targets" / "tb4" / "local").mkdir(parents=True)
    (tmp_path / "targets" / "tb4" / "local" / "options").write_text("OPT_ARCH='x86_64'\n")
    env = {"HOME": str(tmp_path), "QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "state"), "QNX_MCP_ALLOW_TARGETS": "tb*"}
    t = Target(Config.from_env(env), "tb4")
    assert t.host_key_policy() == "unverifiable"
    with pytest.raises(QnxMcpError) as e:
        t._ssh_opts()
    assert e.value.code == "host_key_missing"
    opts = Target(Config.from_env({**env, "QNX_MCP_TRUST_FIRST_USE": "1"}), "tb4")._ssh_opts()
    assert "StrictHostKeyChecking=accept-new" in opts and "HostKeyAlias=qnx-mcp-tb4" in opts


def test_install_never_replaces_or_follows(tmp_path: Path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    src = tmp_path / "src"
    src.write_bytes(b"new")
    _install(src, shared, "a.txt", overwrite=False)
    assert (shared / "a.txt").read_bytes() == b"new" and stat.S_IMODE(os.stat(shared).st_mode) == 0o755
    with pytest.raises(QnxMcpError) as e:
        _install(src, shared, "a.txt", overwrite=False)
    assert e.value.code == "file_exists"
    (tmp_path / "victim").write_bytes(b"keep")
    (shared / "link").symlink_to(tmp_path / "victim")
    with pytest.raises(QnxMcpError):
        _install(src, shared, "link", overwrite=True)
    assert (tmp_path / "victim").read_bytes() == b"keep" and sorted(p.name for p in shared.iterdir()) == ["a.txt", "link"]


def test_core_read_and_build_id(tmp_path: Path):
    bid = bytes(range(16))
    note = struct.pack("<III", 4, len(bid), 3) + b"GNU\0" + bid
    seg = b"\0" * 0x218 + note
    vaddr, off = 0x5550000000, 0x1000
    ehdr = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", 4, 62, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    phdr = struct.pack("<IIQQQQQQ", 1, 4, off, vaddr, 0, len(seg), len(seg), 0x1000)
    (tmp_path / "c.core").write_bytes((ehdr + phdr).ljust(off, b"\0") + seg)
    got = core_read(str(tmp_path / "c.core"), vaddr + 0x218, len(note))
    assert got == note and gnu_build_id(got) == bid.hex()
    assert core_read(str(tmp_path / "c.core"), vaddr + len(seg) - 4, 16) is None


@pytest.mark.parametrize("pid,name,path,allowed", [
    (1, "procnto-smp-instr", "/proc/boot/procnto-smp-instr", False),
    (16387, "slogger2", "/proc/boot/slogger2", False),
    (20000, "procnto-copy", "/tmp/procnto-copy", False),
    (20001, "slay", "/system/bin/slay", False),
    (20002, "x", "/usr/bin/x", False),
    (20003, "x", "/bin/x", False),
    (20004, "x", "/sbin/x", False),
    (20005, "qnx-mcp-run-abc", "/tmp/qnx-mcp/bin/qnx-mcp-run-abc", False),
    (20006, "x", "/etc/x", False),
    (20007, "x", "/tmpx/x", False),                             # prefix of an allowed root, not below it
    (20008, "x", "/tmp/../system/bin/x", False),                # traversal, normalised before the rules
    (20009, "x", "", False),
    (20010, "x", "rel/x", False),                               # not what exefile gives: refused
    (20011, "sleep", "/system/bin/./sleep", False),             # a system program started as ./sleep
    (20012, "hub", "/tmp/hub", True),
    (20013, "statusd", "/data/opt/status/./statusd", True),     # started as ./statusd in /data/opt/status
    (20014, "statusd", "/data/var/tmp/sv/sub/../statusd", True),
    (20015, "crash", "/tmp/qnx-mcp/apps/9216f565e767f4b9/crash", True),
])
def test_stop_refusal(pid, name, path, allowed):
    from qnx_mcp.ops import stop_refusal
    why = stop_refusal(pid, name, path)
    assert (why is None) == allowed, why


def test_program_choice(tmp_path):
    from qnx_mcp.ops import _program
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path), "QNX_MCP_STATE_DIR": str(tmp_path / "s"), "QNX_MCP_ALLOW_TARGETS": "*"})
    for binary, remote, code in ((None, None, "invalid_argument"), ("/bin/ls", "/data/x", "invalid_argument"),
                                 (None, "/tmp/qnx-mcp/apps/a/b", "path_not_allowed"), (None, "/tmp/./x", "invalid_argument"),
                                 (None, "relative/x", "invalid_argument")):
        with pytest.raises(QnxMcpError) as e:
            _program(cfg, None, binary, remote, "r-000000000000")
        assert e.value.code == code, (binary, remote, e.value.message)
    prog = _program(cfg, None, None, "/data/home/svc/sensord", "r-000000000000")
    assert prog["remote_binary"] and prog["remote_path"] == "/data/home/svc/sensord" and prog["binary"] is None


def test_stale_lease_is_rediscovered_after_a_connection_failure(tmp_path, monkeypatch):
    """One live lease is used without a port probe; when ssh cannot connect there, the address is rediscovered
    (leases and mkqnximage --getip) and the command retried once; a session that broke after it started is not."""
    import types
    import qnx_mcp.target as tg
    d = tmp_path / "targets" / "tb5" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:05'\n")
    leases = tmp_path / "leases"
    leases.write_text(json.dumps([{"mac-address": "52:54:00:00:00:05", "ip-address": "10.0.0.5", "expiry-time": 0}]))
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "s"),
                           "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_DHCP_LEASES": str(leases)})
    t = Target(cfg, "tb5")
    monkeypatch.setattr(Target, "_ssh_opts", lambda self: [])
    monkeypatch.setattr(Target, "qemu_pid", lambda self: 4242)
    monkeypatch.setattr(Target, "_getip", lambda self, timeout: "10.0.0.9")
    calls = []

    def fake(argv, **kw):
        host = next(a for a in argv if a.startswith("root@"))
        calls.append(host)
        if host == "root@10.0.0.5":
            return types.SimpleNamespace(returncode=255, timed_out=False, out="", err="ssh: connect to host 10.0.0.5 port 22: No route to host\r\n", truncated=False)
        return types.SimpleNamespace(returncode=0, timed_out=False, out="ok\n", err="", truncated=False)

    monkeypatch.setattr(tg, "run", fake)
    assert t.lookup_ip() == "10.0.0.5"  # no probe
    assert t.ssh(["true"], timeout=5).out == "ok\n" and calls == ["root@10.0.0.5", "root@10.0.0.9"]
    t.ssh(["true"], timeout=5, ip="10.0.0.5")
    assert calls[2:] == ["root@10.0.0.9"]  # later calls go to the rediscovered address directly

    def broken(argv, **kw):
        calls.append("broken")
        return types.SimpleNamespace(returncode=255, timed_out=False, out="", err="Connection to 10.0.0.9 closed by remote host.\r\n", truncated=False)

    monkeypatch.setattr(tg, "run", broken)
    with pytest.raises(QnxMcpError) as e:
        t.ssh(["true"], timeout=5)
    assert e.value.code == "ssh_failed" and calls.count("broken") == 1


def test_stop_result_never_reports_an_unknown_state_as_success():
    from qnx_mcp.ops import stop_result
    proc = {"pid": 7, "name": "hang", "path": "tmp/hang"}
    head = {"runner": "qnx-mcp-run", "pid": 7, "executable": "/tmp/hang", "real_path": "/data/var/tmp/hang", "signal": "SIGTERM",
            "signal_sent": True, "sigkill_sent": False, "already_exiting": False, "waited_ms": 5, "warnings": []}
    for extra, code, words in (({"exited": None, "state_error": "Bad file descriptor"}, "remote_failed", "could not be read"),
                               ({"exited": False, "sigkill_withheld": "it runs a different executable now"}, "process_not_allowed", "SIGKILL was not sent")):
        with pytest.raises(QnxMcpError) as e:
            stop_result("tb", 7, proc, json.dumps({**head, **extra}), 1, "hint")
        assert e.value.code == code and words in e.value.message and e.value.details["signal_sent"] is True, e.value.to_dict()
    for usage, code in (("changed: its start time differs (the pid was reused, or the program called exec)", "process_not_found"),
                        ("changed: it runs a different executable now", "process_not_allowed"), ("no such process", "process_not_found")):
        with pytest.raises(QnxMcpError) as e:
            stop_result("tb", 7, proc, json.dumps({"runner": "qnx-mcp-run", "usage_error": usage, "error": None}), 1, "hint")
        assert e.value.code == code, usage
    ok = stop_result("tb", 7, proc, json.dumps({**head, "exited": True, "state_error": None, "sigkill_withheld": None}), 1, "hint")
    assert ok["exited"] is True and ok["still_running"] is False


def test_list_result_reports_incomplete_and_unverified_listings():
    from qnx_mcp.ops import list_result
    res = {"runner": "qnx-mcp-run", "real_path": "/data/x", "real_path_verified": False, "complete": False, "read_error": "Input/output error",
           "total": 1, "entries": [{"name": "a", "type": "file"}]}
    out = list_result("tb", "/tmp/x", res)
    assert out["complete"] is False and out["truncated"] is True and out["real_path_verified"] is False
    assert "incomplete" in out["note"] and "real_path" in out["note"]
    good = list_result("tb", "/tmp/x", {**res, "real_path_verified": True, "complete": True, "read_error": None})
    assert good["truncated"] is False and "note" not in good


def test_same_executable_ties_pidin_to_exefile():
    from qnx_mcp.ops import same_executable
    assert same_executable("./statusd", "/data/opt/status/./statusd")
    assert same_executable("tmp/hub", "/tmp/hub") and same_executable("/proc/boot/procnto-smp-instr", "/proc/boot/procnto-smp-instr")
    assert same_executable("../hangrel", "/data/var/tmp/rel/sub/../hangrel")
    assert not same_executable("./statusd", "/data/opt/status/./other") and not same_executable("hub", "/tmp/xhub") and not same_executable("x", None)


def test_remote_binary_policy(tmp_path):
    from qnx_mcp.ops import _program, remote_binary_refusal
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path), "QNX_MCP_STATE_DIR": str(tmp_path / "s"), "QNX_MCP_ALLOW_TARGETS": "*"})
    assert cfg.remote_binary_roots == ("/tmp", "/data")
    for path in ("/proc/boot/ksh", "/bin/sh", "/system/bin/slay", "/usr/bin/x", "/sbin/x", "/data/home/ksh", "/tmp/fesh", "/tmp/x/toybox", "/opt/svc/x", "/tmpx/y"):
        assert remote_binary_refusal(cfg, path), path
        with pytest.raises(QnxMcpError) as e:
            _program(cfg, None, None, path, "r-000000000000")
        assert e.value.code == "path_not_allowed", path
    assert remote_binary_refusal(cfg, "/data/opt/status/statusctl") is None and remote_binary_refusal(cfg, "/tmp/sv/stress") is None
    narrow = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path), "QNX_MCP_REMOTE_BINARY_ROOTS": "/data/opt:/bad/../x:relative"})
    assert narrow.remote_binary_roots == ("/data/opt",)
    assert remote_binary_refusal(narrow, "/data/opt/status/statusctl") is None and remote_binary_refusal(narrow, "/tmp/sv/stress")
    closed = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path), "QNX_MCP_REMOTE_BINARY_ROOTS": ""})
    assert closed.remote_binary_roots == () and "QNX_MCP_REMOTE_BINARY_ROOTS" in remote_binary_refusal(closed, "/tmp/x")


# ---------------------------------------------------------------- build arguments and the workspace

def _fake_sdp(tmp_path: Path) -> tuple[Config, Path, Path]:
    """An SDP whose qcc, q++, make and cmake only record their argv, so a test sees exactly what build() would run."""
    from qnx_mcp import sdk
    sdk._env_cache.clear()
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    log = tmp_path / "argv.log"
    for tool in ("qcc", "q++", "make", "cmake"):
        (bin_ / tool).write_text(f'#!/bin/sh\nprintf "%s\\n" "{tool}" "$@" "--end--" >> "{log}"\n')
        (bin_ / tool).chmod(0o755)
    env_sh = tmp_path / "qnxsdp-env.sh"
    env_sh.write_text(f'export QNX_HOST={tmp_path}/host QNX_TARGET={tmp_path}/target PATH="{bin_}:$PATH"\n')
    ws = (tmp_path / "ws").resolve()
    (ws / "proj" / "include").mkdir(parents=True)
    (ws / "proj" / "main.c").write_text("int main(void) { return 0; }\n")
    cfg = Config.from_env({"HOME": str(tmp_path), "QNX_MCP_SDP_ENV": str(env_sh), "QNX_MCP_STATE_DIR": str(tmp_path / "state"),
                           "QNX_MCP_WORKSPACE": str(ws), "PATH": os.environ["PATH"]})
    return cfg, ws, log


def _recorded(log: Path) -> list[list[str]]:
    calls, cur = [], []
    for line in log.read_text().splitlines() if log.exists() else []:
        if line == "--end--":
            calls.append(cur)
            cur = []
        else:
            cur.append(line)
    return calls


def test_build_passes_allowed_arguments(tmp_path: Path):
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    cflags = ["-g", "-O0", "-Wall", "-Wextra", "-Werror=format", "-Wno-unused", "-DREBUILT=1", "-UNDEBUG", "-fno-inline", "-fPIC",
              "-std=c11", "-march=x86-64", "-pthread", "-I" + str(proj / "include"), "-Iinclude"]
    ldflags = ["-Wl,--build-id=none", "-lsocket", "-lm", "-L" + str(proj)]
    r = build(cfg, str(proj), system="qcc", cflags=cflags, ldflags=ldflags)
    assert r["ok"], r
    (qcc,) = _recorded(log)
    for a in cflags + ldflags:
        assert a in qcc, (a, qcc)
    (proj / "Makefile").write_text("all:\n\t@:\n")
    make_args = ["all", "clean", "-k", "-j4", "-j", "2", "-s", "CFLAGS=-O2 -g -DX=1", "LDLIBS=-lsocket", "V=1"]
    assert build(cfg, str(proj), system="make", make_args=make_args)["ok"]
    make = _recorded(log)[1]
    assert make[:3] == ["make", "-C", str(proj)] and make[4:] == make_args, make
    assert build(cfg, str(proj / "main.c"), cflags=["-g", "-O2"])["ok"]  # the aarch64/qcc flow of test_build_sdk.py


def test_build_cmake_arguments(tmp_path: Path, monkeypatch):
    from qnx_mcp import sdk
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "CMakeLists.txt").write_text("project(x C)\n")
    monkeypatch.setattr(sdk.shutil, "which", lambda name, path=None: str(tmp_path / "fakebin" / name))
    r = sdk.build(cfg, str(proj), cflags=["-g", "-O1"], make_args=["--target", "hello", "--clean-first", "-v"])
    assert r["ok"], r
    configure, build_ = _recorded(log)
    assert "-DCMAKE_C_FLAGS=-g -O1" in configure and build_[-4:] == ["--target", "hello", "--clean-first", "-v"], (configure, build_)
    for bad in (["--", "-f", "x.mk"], ["--target", "$(id)"], ["-DFOO=1"]):
        with pytest.raises(QnxMcpError) as e:
            sdk.build(cfg, str(proj), make_args=bad)
        assert e.value.code == "invalid_argument"
    assert len(_recorded(log)) == 2


def test_build_flags_reach_every_mode(tmp_path: Path, monkeypatch):
    """make mode would drop cflags and ldflags, so they are refused there; cmake sets C, C++ and linker flags on
    every configure, empty when omitted, so flags from an earlier call do not stay in its cache."""
    from qnx_mcp import sdk
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "Makefile").write_text("all:\n\t@:\n")
    for field, hint in (("cflags", "CFLAGS="), ("ldflags", "LDFLAGS=")):
        with pytest.raises(QnxMcpError) as e:
            sdk.build(cfg, str(proj), system="make", **{field: ["-O2" if field == "cflags" else "-lm"]})
        assert e.value.code == "invalid_argument" and field in e.value.message and hint in (e.value.hint or ""), e.value.to_dict()
    assert _recorded(log) == []
    (proj / "CMakeLists.txt").write_text("project(x C CXX)\n")
    monkeypatch.setattr(sdk.shutil, "which", lambda name, path=None: str(tmp_path / "fakebin" / name))
    assert sdk.build(cfg, str(proj), system="cmake", cflags=["-g", "-O1"], ldflags=["-lm"])["ok"]
    assert sdk.build(cfg, str(proj), system="cmake")["ok"]
    first, _, second, _ = _recorded(log)
    for want in ("-DCMAKE_C_FLAGS=-g -O1", "-DCMAKE_CXX_FLAGS=-g -O1", "-DCMAKE_EXE_LINKER_FLAGS=-lm"):
        assert want in first, (want, first)
    for want in ("-DCMAKE_C_FLAGS=", "-DCMAKE_CXX_FLAGS=", "-DCMAKE_EXE_LINKER_FLAGS="):
        assert want in second, (want, second)


def test_cmake_toolchain_file_never_follows_a_link(tmp_path: Path, monkeypatch):
    """A link planted at the toolchain file's name must be replaced, never written through."""
    from qnx_mcp import sdk
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "CMakeLists.txt").write_text("project(x C)\n")
    monkeypatch.setattr(sdk.shutil, "which", lambda name, path=None: str(tmp_path / "fakebin" / name))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep me\n")
    bdir = proj / "build-qnx-x86_64"
    bdir.mkdir()
    bdir.chmod(0o755)
    (bdir / "qnx-toolchain.cmake").symlink_to(outside)
    assert sdk.build(cfg, str(proj))["ok"]
    tc = bdir / "qnx-toolchain.cmake"
    assert outside.read_bytes() == b"keep me\n"
    assert not tc.is_symlink() and tc.is_file() and "CMAKE_SYSTEM_NAME QNX" in tc.read_text()
    assert stat.S_IMODE(bdir.stat().st_mode) == 0o755  # the user's build directory is not made private


# Each of these could make the host run a command or touch a file outside the project; build() must refuse it before running anything.
REJECTED = [
    ("make_args", "--eval=$(info QNXMCP_MARKER)"),
    ("make_args", "-f"),
    ("make_args", "-fother.mk"),
    ("make_args", "--file=other.mk"),
    ("make_args", "--makefile=other.mk"),
    ("make_args", "-C"),
    ("make_args", "--directory=/"),
    ("make_args", "-I/tmp"),
    ("make_args", "-e"),
    ("make_args", "SHELL=/bin/sh"),
    ("make_args", ".SHELLFLAGS=-c"),
    ("make_args", "MAKEFLAGS=-f/x"),
    ("make_args", "MAKEFILES=/tmp/x.mk"),
    ("make_args", "CC=/tmp/cc"),
    ("make_args", "CFLAGS=-O2;touch x"),
    ("make_args", "CFLAGS=$(info x)"),
    ("make_args", "CFLAGS=`id`"),
    ("make_args", "CFLAGS=-wrapper,/bin/sh"),
    ("make_args", "V=$(info x)"),
    ("make_args", "all\nclean"),
    ("make_args", "../outside"),
    ("make_args", "/abs/target"),
    ("make_args", "-j0x"),
    ("cflags", "-wrapper"),
    ("cflags", "-wrapper,/bin/sh,-c"),
    ("cflags", "-specs=x.specs"),
    ("cflags", "--specs=x.specs"),
    ("cflags", "-fplugin=./x.so"),
    ("cflags", "-fplugin-arg-x-y=z"),
    ("cflags", "-B/tmp"),
    ("cflags", "-B"),
    ("cflags", "@args.txt"),
    ("cflags", "-Wa,-o,/tmp/x"),
    ("cflags", "-Wp,-MD,/tmp/x"),
    ("cflags", "-Xlinker"),
    ("cflags", "-Xassembler"),
    ("cflags", "-o"),
    ("cflags", "-o/tmp/x"),
    ("cflags", "-include"),
    ("cflags", "-imacros"),
    ("cflags", "-I/etc"),
    ("cflags", "-I../.."),
    ("cflags", "-iquote/etc"),
    ("cflags", "-isystem"),
    ("cflags", "--sysroot=/"),
    ("cflags", "-Vgcc_ntox86_64"),
    ("cflags", "-DX=$(id)"),
    ("cflags", "-DX=`id`"),
    ("cflags", "-DX=a;b"),
    ("cflags", "-fdump-tree-all=/tmp/x"),
    ("cflags", "-fprofile-generate=/tmp/p"),
    ("cflags", "-fprofile-use=../p"),
    ("cflags", "-fuse-ld=/tmp/ld"),
    ("cflags", "-MF/tmp/deps"),
    ("cflags", "-save-temps=/tmp"),
    ("cflags", "-x"),
    ("cflags", "main.c"),
    ("cflags", "-O2 -g"),
    ("ldflags", "-Wl,-T,/tmp/x.ld"),
    ("ldflags", "-Wl,-Map=/tmp/map"),
    ("ldflags", "-Wl,--build-id=none,-T,x.ld"),
    ("ldflags", "-Wl,--wrap=open"),
    ("ldflags", "-T"),
    ("ldflags", "-L/etc"),
    ("ldflags", "/etc/x.o"),
    ("ldflags", "-l../x"),
    ("ldflags", "-Xlinker"),
    ("sources", "-wrapper"),
    ("sources", "@args.txt"),
]


@pytest.mark.parametrize("field,arg", REJECTED, ids=[f"{f}:{a!r}" for f, a in REJECTED])
def test_build_rejects_unsafe_arguments(tmp_path: Path, field: str, arg: str):
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    system = "make" if field == "make_args" else "qcc"
    (proj / "Makefile").write_text("all:\n\t@:\n")
    with pytest.raises(QnxMcpError) as e:
        build(cfg, str(proj), system=system, **{field: [arg]})
    assert e.value.code in ("invalid_argument", "path_not_allowed"), e.value.to_dict()
    assert repr(arg) in e.value.message and field in e.value.message, e.value.message
    assert _recorded(log) == [], "nothing may run when an argument is refused"


def test_build_output_symlink_escape(tmp_path: Path):
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    (ws / "proj" / "out").symlink_to(tmp_path / "elsewhere")
    for output in (str(ws / "proj" / "out"), str(tmp_path / "x"), "../../x"):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(ws / "proj"), system="qcc", output=output)
        assert e.value.code == "path_not_allowed", output
    assert _recorded(log) == []


def test_workspace_defaults_to_the_working_directory(tmp_path: Path, monkeypatch):
    from qnx_mcp.ops import environment_check
    ws = tmp_path / "proj"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "a.c").write_text("x\n")
    (tmp_path / "secret.txt").write_text("s\n")
    (ws / "sub" / "link.txt").symlink_to(tmp_path / "secret.txt")
    monkeypatch.chdir(ws / "sub")
    monkeypatch.chdir(ws)
    for env in ({"HOME": str(tmp_path)}, {"HOME": str(tmp_path), "QNX_MCP_WORKSPACE": ""}):
        cfg = Config.from_env(env)
        assert cfg.workspace == (Path(os.path.realpath(ws)),), cfg.workspace
        assert check_host_path(cfg, str(ws / "sub" / "a.c"), "f") == Path(os.path.realpath(ws / "sub" / "a.c"))
        for p in (tmp_path / "secret.txt", ws / "sub" / "link.txt", ws / ".." / "secret.txt"):
            with pytest.raises(QnxMcpError) as e:
                check_host_path(cfg, str(p), "local_path")
            assert e.value.code == "path_not_allowed", p
        assert _source_context(cfg, str(tmp_path / "secret.txt"), 1) is None
    check = {c["check"]: c for c in environment_check(Config.from_env({"HOME": str(tmp_path), "QNX_MCP_SDP_ENV": str(tmp_path / "none")}))["checks"]}
    assert check["workspace"]["ok"] and os.path.realpath(ws) in check["workspace"]["detail"] and "working directory" in check["workspace"]["detail"]
    explicit = Config.from_env({"HOME": str(tmp_path), "QNX_MCP_WORKSPACE": str(tmp_path / "secret.txt") + os.pathsep + str(ws)})
    assert len(explicit.workspace) == 2
    monkeypatch.chdir("/")
    root = {c["check"]: c for c in environment_check(Config.from_env({"HOME": str(tmp_path), "QNX_MCP_SDP_ENV": str(tmp_path / "none")}))["checks"]}
    assert not root["workspace"]["ok"] and "fix" in root["workspace"]


def test_package_version_matches_pyproject():
    """The release workflow builds tag v<version>; pyproject.toml and __version__ must agree."""
    import re
    from qnx_mcp import __version__
    toml = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert re.search(r'(?m)^version = "([^"]+)"$', toml).group(1) == __version__


def test_environment_check_points_hosts_without_kvm_to_ssh_mode(tmp_path: Path, monkeypatch):
    from qnx_mcp import ops
    cfg = Config.from_env({"HOME": str(tmp_path), "QNX_MCP_WORKSPACE": str(tmp_path)})
    real_exists = os.path.exists
    monkeypatch.setattr(ops.os.path, "exists", lambda p: False if p == "/dev/kvm" else real_exists(p))
    kvm = {c["check"]: c for c in ops.environment_check(cfg)["checks"]}["kvm"]
    assert not kvm["ok"] and "ssh -T" in kvm["fix"] and "adduser" not in kvm["fix"], kvm
    monkeypatch.setattr(ops.os.path, "exists", lambda p: True if p == "/dev/kvm" else real_exists(p))
    monkeypatch.setattr(ops.os, "access", lambda p, mode: False)
    kvm = {c["check"]: c for c in ops.environment_check(cfg)["checks"]}["kvm"]
    assert not kvm["ok"] and "adduser" in kvm["fix"], kvm


def test_build_passes_separate_and_wider_forms(tmp_path: Path):
    """MCP-09: forms that are safe when checked as pairs or with a constrained value."""
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "config.h").write_text("#define X 1\n")
    cflags = ["-g", "-I", str(proj / "include"), "-I", "include", "-D", "NAME", "-D", "LEVEL=2", "-U", "NDEBUG",
              "-isystem", str(proj / "include"), "-iquote", "include", "-include", "config.h", '-DVERSION="1.2"',
              "-D", 'GREETING="hi"', f"-fdebug-prefix-map={proj}=/src", f"-ffile-prefix-map={proj}=."]
    ldflags = ["-L", str(proj), "-Wl,--build-id,--as-needed", f"-Wl,-Map={proj / 'out.map'}"]
    r = build(cfg, str(proj), system="qcc", cflags=cflags, ldflags=ldflags)
    assert r["ok"], r
    (qcc,) = _recorded(log)
    i = qcc.index("-g")
    assert qcc[i:i + len(cflags)] == cflags and qcc[-len(ldflags):] == ldflags, qcc
    (proj / "Makefile").write_text("all:\n\t@:\n")
    make_args = ["install", "-j", "4", f"PREFIX={proj / 'stage'}", f"DESTDIR={proj / 'dest'}", "CFLAGS=-I include -D X=1 -g"]
    assert build(cfg, str(proj), system="make", make_args=make_args)["ok"]
    assert _recorded(log)[1][4:] == make_args


REJECTED_GROUPS = [
    ("cflags", ["-include", "/etc/passwd"]),
    ("cflags", ["-include", "../../x.h"]),
    ("cflags", ["-include", "include"]),  # a directory, not a file
    ("cflags", ["-isystem", "../.."]),
    ("cflags", ["-isystem", "/etc"]),
    ("cflags", ["-iquote", "/usr/include"]),
    ("cflags", ["-I", "/etc"]),
    ("cflags", ["-I", "-wrapper"]),
    ("cflags", ["-D", "X=$(id)"]),
    ("cflags", ["-D", "-wrapper"]),
    ("cflags", ["-D"]),
    ("cflags", ["-U", "X=1"]),
    ("cflags", ['-DX="a`id`"']),
    ("cflags", ['-DX="a;b"']),
    ("cflags", ['-DX="a\\b"']),
    ("cflags", ['-DX="a\nb"']),
    ("cflags", ["-fdebug-prefix-map=../x=/y"]),
    ("cflags", ["-fdebug-prefix-map=$x=/y"]),
    ("cflags", ["-fdebug-prefix-map=/x"]),
    ("ldflags", ["-L", "/usr/lib"]),
    ("ldflags", ["-Wl,-rpath,/data/lib"]),  # ld also searches -rpath dirs on the build host at link time
    ("cflags", ['-DMSG="hello world"']),
    ("cflags", ['-DX="a>b"']),
    ("cflags", ["-DX='a'"]),
    ("cflags", ['-DX="a -wrapper /tmp/x"']),
    ("ldflags", ["-Wl,-Map=../out.map"]),
    ("make_args", ["PREFIX=/usr"]),
    ("make_args", ["PREFIX=../stage"]),
    ("make_args", ["DESTDIR=/"]),
    ("make_args", ["CROSS_COMPILE=x86_64-pc-nto-qnx8.0.0-"]),
    ("make_args", ['CFLAGS=-DMSG="a b"']),
    ("make_args", ["CFLAGS=-include /etc/passwd"]),
    ("make_args", ["CFLAGS=-I"]),
]


@pytest.mark.parametrize("field,args", REJECTED_GROUPS, ids=[f"{f}:{a!r}" for f, a in REJECTED_GROUPS])
def test_build_rejects_unsafe_pairs_and_values(tmp_path: Path, field: str, args: list[str]):
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    system = "make" if field == "make_args" else "qcc"
    (proj / "Makefile").write_text("all:\n\t@:\n")
    with pytest.raises(QnxMcpError) as e:
        build(cfg, str(proj), system=system, **{field: args})
    assert e.value.code in ("invalid_argument", "path_not_allowed"), e.value.to_dict()
    assert field in e.value.message, e.value.message
    assert _recorded(log) == [], "nothing may run when an argument is refused"


def test_quoted_define_values_are_qcc_only(tmp_path: Path, monkeypatch):
    """make and cmake hand CFLAGS to a shell in their recipes, so a value with quotes or spaces is refused there."""
    from qnx_mcp import sdk
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "CMakeLists.txt").write_text("project(x C)\n")
    monkeypatch.setattr(sdk.shutil, "which", lambda name, path=None: str(tmp_path / "fakebin" / name))
    for flags in (['-DMSG="a"'], ["-D", 'MSG="x"']):
        with pytest.raises(QnxMcpError) as e:
            sdk.build(cfg, str(proj), system="cmake", cflags=flags)
        assert e.value.code == "invalid_argument" and "qcc" in e.value.message, e.value.message
    assert _recorded(log) == []


def _literal_build_calls() -> list[tuple[str, str, dict]]:
    """Every build call in the SDK and end-to-end tests whose flag arguments are literals."""
    import ast
    found = []
    here = Path(__file__).parent
    for f in sorted(here.glob("test_*e2e.py")) + [here / "test_build_sdk.py"]:
        for node in ast.walk(ast.parse(f.read_text())):
            if not isinstance(node, ast.Call):
                continue
            is_tool = node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "build"
            is_fn = isinstance(node.func, ast.Name) and node.func.id in ("build", "_build")
            if not (is_tool or is_fn):
                continue
            kw = {}
            for k in node.keywords:
                if k.arg in ("cflags", "ldflags", "make_args", "system"):
                    kw[k.arg] = ast.literal_eval(k.value)
            found.append((f.name, node.lineno, kw))
    return found


def test_live_test_build_arguments_pass_the_allow_list(tmp_path: Path):
    """The SDK and end-to-end tests never ran after the allow-list was added; each of their build arguments must pass it."""
    from qnx_mcp.sdk import check_build_args
    cfg, ws, _ = _fake_sdp(tmp_path)
    calls = _literal_build_calls()
    assert len(calls) >= 15 and any(kw for _, _, kw in calls), calls
    for name, line, kw in calls:
        check_build_args(cfg, ws / "proj", kw.get("system", "qcc"), kw.get("cflags"), kw.get("ldflags"), kw.get("make_args"))


def _mi(exe: str | None = "/tmp/qnx-mcp/apps/abc/crash", pid: int | None = 42, note: bool = False) -> dict:
    console = [] if exe is None else [f"Core was generated by `{exe}'.\n"]
    if note:
        console += ["Local exec file:\n", "\t0x400200 - 0x400224 is .note.gnu.build-id\n", "Local core dump file:\n"]
    target_id = f"pid {pid} tid 1" if pid is not None else "tid 1"
    return {"console": console, "results": {"1": {"class": "done", "threads": [{"id": "1", "target-id": target_id}]}}}


_RUN_REC = {"run_id": "r-000000000001", "remote_binary": False, "remote_path": "/tmp/qnx-mcp/apps/abc/crash", "remote_real_path": None, "pid": 42}
_CORE_INFO = {"sha256": "c" * 64, "size": 4096}


@pytest.mark.parametrize("rec,mi", [
    (_RUN_REC, _mi(exe=None)),  # gdb named no executable: absent evidence is not a match
    ({**_RUN_REC, "pid": None}, _mi(pid=None)),  # neither side has a pid
], ids=["no-executable-line", "no-pid-either-side"])
def test_provenance_needs_the_core_to_name_executable_and_pid(rec, mi):
    from qnx_mcp.crash import _provenance
    with pytest.raises(QnxMcpError) as e:
        _provenance(Path("core"), Path("bin"), {}, "b" * 64, rec, mi, _CORE_INFO)
    assert e.value.code == "core_mismatch", e.value.to_dict()


# ---------------------------------------------------------------- provenance, uploads and run state, offline

_REMOTE_REC = {"run_id": "r-000000000002", "remote_binary": True, "remote_path": "/data/opt/crash", "remote_real_path": "/data/var/opt/crash",
               "pid": 42}

PROVENANCE = [
    # id, record, mi, binary build ID, core build ID, expected: "mismatch" or (verified, evidence prefix)
    ("run-match", _RUN_REC, _mi(), None, None, (True, "executable path and pid match")),
    ("run-build-id", _RUN_REC, _mi(note=True), "aa", "aa", (True, "build ID, executable path and pid match")),
    ("run-wrong-exe", _RUN_REC, _mi(exe="/tmp/qnx-mcp/apps/def/crash"), None, None, "mismatch"),
    ("run-wrong-pid", _RUN_REC, _mi(pid=7), None, None, "mismatch"),
    ("run-build-id-differs", _RUN_REC, _mi(note=True), "aa", "bb", "mismatch"),
    ("run-two-pids", _RUN_REC, {**_mi(), "results": {"1": {"threads": [{"target-id": "pid 42 tid 1"}, {"target-id": "pid 43 tid 1"}]}}},
     None, None, "mismatch"),
    ("manual-build-id", None, _mi(note=True), "aa", "aa", (True, "build ID, match")),
    ("manual-binary-without-id", None, _mi(note=True), None, "aa", "mismatch"),
    ("manual-core-without-note", None, _mi(), "aa", None, "mismatch"),
    ("manual-build-id-differs", None, _mi(note=True), "aa", "bb", "mismatch"),
    ("remote-build-id", _REMOTE_REC, _mi(exe="/data/opt/crash", note=True), "aa", "aa", (True, "build ID, executable path and pid match")),
    ("remote-real-path", _REMOTE_REC, _mi(exe="/data/var/opt/crash", note=True), "aa", "aa", (True, "build ID")),
    ("remote-no-build-id", _REMOTE_REC, _mi(exe="/data/opt/crash"), None, None, (False, "pid and path match; build ID not available")),
    ("remote-core-without-note", _REMOTE_REC, _mi(exe="/data/opt/crash"), "aa", None, (False, "pid and path match; build ID not available")),
    ("remote-build-id-differs", _REMOTE_REC, _mi(exe="/data/opt/crash", note=True), "aa", "bb", "mismatch"),
    ("remote-wrong-exe", _REMOTE_REC, _mi(exe="/data/opt/other", note=True), "aa", "aa", "mismatch"),
]


@pytest.mark.parametrize("rec,mi,bin_bid,core_bid,expected", [c[1:] for c in PROVENANCE], ids=[c[0] for c in PROVENANCE])
def test_provenance_table(monkeypatch, rec, mi, bin_bid, core_bid, expected):
    from qnx_mcp import crash
    monkeypatch.setattr(crash, "core_read", lambda path, off, n: b"note" if core_bid else None)
    monkeypatch.setattr(crash, "gnu_build_id", lambda note: core_bid)
    args = (Path("core"), Path("bin"), {"build_id": bin_bid} if bin_bid else {}, "b" * 64, rec, mi, _CORE_INFO)
    if expected == "mismatch":
        with pytest.raises(QnxMcpError) as e:
            crash._provenance(*args)
        assert e.value.code == "core_mismatch" and "provenance" in e.value.details, e.value.to_dict()
        return
    ev = crash._provenance(*args)
    assert ev["verified"] is expected[0] and ev["evidence"].startswith(expected[1]), ev
    assert ev["source"] == ("remote_run" if rec and rec["remote_binary"] else "run_record" if rec else "manual")
    assert ev["core_sha256"] == _CORE_INFO["sha256"] and ev["binary_sha256"] == "b" * 64


def _reply(out: str = "", code: int = 0) -> Any:
    import types
    return types.SimpleNamespace(out=out, err="", returncode=code, truncated=False, timed_out=False)


def _runner_json(**kw: Any) -> str:
    return json.dumps({"runner": "qnx-mcp-run", **kw})


class FakeRemote:
    """Replaces a Target's put, ssh and ssh_script. Answers come from a queue in call order; a QnxMcpError in
    the queue is raised. ssh with check=True fails like the real one when the answer has a nonzero status."""

    def __init__(self, t: Target, answers: list[Any]):
        self.calls: list[tuple[str, Any]] = []
        self.answers = list(answers)
        t.put = self.put
        t.ssh = self.ssh
        t.ssh_script = self.ssh_script

    def _next(self, kind: str, what: Any, check: bool = False) -> Any:
        self.calls.append((kind, what))
        a = self.answers.pop(0)
        if isinstance(a, QnxMcpError):
            raise a
        if check and a.returncode != 0:
            raise QnxMcpError("remote_failed", f"{kind} exited with status {a.returncode}")
        return a

    def put(self, local, remote, **kw):
        self._next("put", (str(local), remote))

    def ssh(self, argv, *, check=True, **kw):
        return self._next("ssh", list(argv), check)

    def ssh_script(self, script, *, check=True, **kw):
        return self._next("script", script, check)


@pytest.fixture
def fake_target(tmp_path: Path, monkeypatch):
    from qnx_mcp import ops
    d = tmp_path / "targets" / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:01'\n")
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "s"),
                           "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path)})
    runner = tmp_path / "qnx-mcp-run"
    runner.write_bytes(b"runner bytes")
    monkeypatch.setattr(ops, "runner_binary", lambda cfg_, arch: (runner, "d1g3st"))
    app = tmp_path / "app"
    app.write_bytes(b"app bytes")
    return Target(cfg, "tb1"), runner, app


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_upload_verifies_then_renames(fake_target):
    from qnx_mcp.ops import _upload
    t, _, app = fake_target
    sha, size = _sha(app), app.stat().st_size
    fake = FakeRemote(t, [_reply(), _reply(_runner_json(sha256=sha, size=size, mode="0755")), _reply()])
    _upload(t, "10.0.0.1", app, "/tmp/qnx-mcp/apps/x/app", sha, size, "0755", "/tmp/qnx-mcp/bin/r")
    (k1, (local, tmp)), (k2, script), (k3, mv) = fake.calls
    assert k1 == "put" and local == str(app) and tmp.startswith("/tmp/qnx-mcp/apps/x/app.part-")
    assert k2 == "script" and script == f"chmod 0755 {tmp} && /tmp/qnx-mcp/bin/r -H {tmp}"
    assert k3 == "ssh" and mv == ["mv", "-f", tmp, "/tmp/qnx-mcp/apps/x/app"]


@pytest.mark.parametrize("got", [{"sha256": "0" * 64}, {"size": 1}, {"mode": "0644"}], ids=["sha", "size", "mode"])
def test_upload_mismatch_removes_the_copy_and_never_renames(fake_target, got):
    from qnx_mcp.ops import _upload
    t, runner, _ = fake_target
    sha, size = _sha(runner), runner.stat().st_size
    fake = FakeRemote(t, [_reply(), _reply(_runner_json(**{"sha256": sha, "size": size, "mode": "0755", **got})), _reply()])
    with pytest.raises(QnxMcpError) as e:
        _upload(t, "10.0.0.1", runner, "/tmp/qnx-mcp/bin/r", sha, size, "0755", None)
    assert e.value.code == "deploy_failed", e.value.to_dict()
    tmp = fake.calls[0][1][1]
    assert fake.calls[1][1] == f"chmod 0755 {tmp} && {tmp} -H {tmp}"  # the runner hashes its own uploaded copy
    assert fake.calls[2] == ("ssh", ["rm", "-f", tmp]) and len(fake.calls) == 3


def _app_entry(app: Path, mode: str = "0755") -> tuple[Path, str, str, int, str]:
    return (app, "/tmp/qnx-mcp/apps/0123456789abcdef/app", _sha(app), app.stat().st_size, mode)


def test_prepare_uploads_nothing_when_everything_matches(fake_target):
    from qnx_mcp.ops import prepare
    t, runner, app = fake_target
    entry = _app_entry(app)
    lines = [_runner_json(sha256=_sha(runner), size=runner.stat().st_size, mode="0755"), _runner_json(sha256=entry[2], size=entry[3], mode="0755")]
    fake = FakeRemote(t, [_reply("\n".join(lines) + "\n")])
    assert prepare(t, "10.0.0.1", [entry], ["/tmp/qnx-mcp/runs/r-000000000001"]) == "/tmp/qnx-mcp/bin/qnx-mcp-run-d1g3st"
    (kind, script), = fake.calls
    assert kind == "script" and script.startswith("{ test -L /tmp/qnx-mcp || ")
    assert "test -O /tmp/qnx-mcp/bin " in script
    assert "/tmp/qnx-mcp/runs/r-000000000001" in script and " -G " not in script


def test_prepare_uploads_a_missing_runner_and_a_wrong_mode_app(fake_target):
    from qnx_mcp.ops import prepare
    t, runner, app = fake_target
    entry = _app_entry(app)
    rsha, rsize = _sha(runner), runner.stat().st_size
    check = _reply("none\n" + _runner_json(sha256=entry[2], size=entry[3], mode="0700") + "\n")
    fake = FakeRemote(t, [check,
                          _reply(), _reply(_runner_json(sha256=rsha, size=rsize, mode="0755")), _reply(),
                          _reply(), _reply(_runner_json(sha256=entry[2], size=entry[3], mode="0755")), _reply()])
    runner_remote = prepare(t, "10.0.0.1", [entry], [])
    puts = [c[1] for c in fake.calls if c[0] == "put"]
    assert [local for local, _ in puts] == [str(runner), str(app)]
    assert puts[0][1].startswith(runner_remote + ".part-") and puts[1][1].startswith(entry[1] + ".part-")
    assert fake.calls[5][1].endswith(f"{runner_remote} -H {puts[1][1]}")  # the app is hashed by the runner
    assert not fake.answers


def test_prepare_refuses_output_it_does_not_recognise(fake_target):
    from qnx_mcp.ops import prepare
    t, _, app = fake_target
    FakeRemote(t, [_reply("none\n")])
    with pytest.raises(QnxMcpError) as e:
        prepare(t, "10.0.0.1", [_app_entry(app)], [])
    assert e.value.code == "remote_failed"


def test_prepare_prune_keeps_runs_in_flight(fake_target):
    from qnx_mcp.ops import prepare, save_record
    t, runner, _ = fake_target
    save_record(t, {"run_id": "r-00000000000a", "state": "running", "started_at": time.time(), "remote_path": "/tmp/qnx-mcp/apps/feedfacefeedface/x"})
    save_record(t, {"run_id": "r-00000000000b", "state": "finished", "started_at": time.time()})
    fake = FakeRemote(t, [_reply(_runner_json(sha256=_sha(runner), size=runner.stat().st_size, mode="0755") + "\n")])
    prepare(t, "10.0.0.1", [], ["/tmp/qnx-mcp/runs/r-00000000000c"], prune=True)
    script = fake.calls[0][1]
    assert "-G /tmp/qnx-mcp/runs -k 20 -a 300 -p r-00000000000a -p r-00000000000c " in script
    assert "-G /tmp/qnx-mcp/apps -k 20 -a 600 -p feedfacefeedface " in script and "r-00000000000b" not in script


OUTCOMES = [
    ({}, "exited"),
    ({"exit_code": 3}, "exited"),
    ({"signaled": True, "core_expected": True}, "crashed"),
    ({"signaled": True}, "killed"),
    ({"disconnected": True, "signaled": True}, "stopped"),
    ({"runner_stopped_by": "SIGTERM", "signaled": True, "core_expected": True}, "stopped"),
    ({"timed_out": True, "signaled": True, "core_expected": True}, "timeout"),
    ({"exec_error": "ENOENT", "timed_out": True}, "exec_failed"),
]


@pytest.mark.parametrize("res,outcome", OUTCOMES)
def test_outcome_precedence(res, outcome):
    from qnx_mcp.ops import _outcome
    assert _outcome(res) == outcome


def test_apply_poll_moves_records_through_their_states(fake_target):
    from qnx_mcp.ops import _apply_poll, load_record, save_record
    t, _, _ = fake_target
    rec = {"run_id": "r-000000000010", "kind": "background", "state": "running", "started_at": time.time()}
    save_record(t, dict(rec))
    assert _apply_poll(t, rec, {"alive": True, "status": None}) == "running"
    assert load_record(t, rec["run_id"])["state"] == "running"
    status = {"exit_code": 0, "pid": 9, "signaled": False}
    assert _apply_poll(t, rec, {"alive": False, "status": status}) == "finished"
    saved = load_record(t, rec["run_id"])
    assert saved["state"] == "finished" and saved["outcome"] == "exited" and saved["pid"] == 9
    first = saved["finished_at"]
    assert _apply_poll(t, rec, {"alive": False, "status": status}) == "finished"
    assert load_record(t, rec["run_id"])["finished_at"] == first  # a finished record is not rewritten
    lost = {"run_id": "r-000000000011", "kind": "background", "state": "running", "started_at": time.time()}
    save_record(t, dict(lost))
    assert _apply_poll(t, lost, {"alive": False, "status": None}) == "lost"
    assert load_record(t, lost["run_id"])["state"] == "lost"


def test_reconcile_stops_only_old_unacknowledged_starts(fake_target, monkeypatch):
    from qnx_mcp import ops
    t, _, _ = fake_target
    now = time.time()
    base = {"kind": "background", "state": "starting", "runner": "/tmp/qnx-mcp/bin/r"}
    for rid, age in (("r-000000000020", 120), ("r-000000000021", 5), ("r-000000000022", 120)):
        ops.save_record(t, {**base, "run_id": rid, "started_at": now - age, "remote_run_dir": f"/tmp/qnx-mcp/runs/{rid}"})
    ops.save_record(t, {**base, "run_id": "r-000000000023", "state": "running", "started_at": now - 120, "remote_run_dir": "/x"})
    stopped = []
    monkeypatch.setattr(ops, "_stop_quietly", lambda t_, ip, runner, d: stopped.append(d) or "stopped")
    ops._reconcile_starts(t, "10.0.0.1", exclude="r-000000000022")
    assert stopped == ["/tmp/qnx-mcp/runs/r-000000000020"]
    states = {rid: ops.load_record(t, rid) for rid in ("r-000000000020", "r-000000000021", "r-000000000022", "r-000000000023")}
    assert states["r-000000000020"]["state"] == "failed" and states["r-000000000020"]["cleanup"] == "stopped"
    assert states["r-000000000020"]["error"] == "start not acknowledged"
    assert [states[r]["state"] for r in ("r-000000000021", "r-000000000022", "r-000000000023")] == ["starting", "starting", "running"]


def _raised_error_codes() -> set[str]:
    """Every code string that src/ can put in an error: QnxMcpError's first argument (a constant, both branches
    of a conditional, or what was assigned to a local name passed there) and the "code" values of dict literals."""
    import ast

    def codes_of(node: ast.AST | None, assigned: dict[str, set[str]]) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return codes_of(node.body, assigned) | codes_of(node.orelse, assigned)
        if isinstance(node, ast.Name):
            return assigned.get(node.id, set())
        return set()

    codes: set[str] = set()
    for f in (Path(__file__).resolve().parents[1] / "src" / "qnx_mcp").glob("*.py"):
        tree = ast.parse(f.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            assigned: dict[str, set[str]] = {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    for tgt in n.targets:
                        if isinstance(tgt, ast.Name):
                            assigned.setdefault(tgt.id, set()).update(codes_of(n.value, {}))
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "QnxMcpError" and n.args:
                    codes |= codes_of(n.args[0], assigned)
                elif isinstance(n, ast.Dict):
                    codes |= {c for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "code"
                              for c in codes_of(v, {})}
    return codes


def test_every_raised_error_code_is_advertised():
    from qnx_mcp.server import ErrorInfo
    raised = _raised_error_codes()
    assert len(raised) >= 30 and "host_key_missing" in raised, sorted(raised)
    design = (Path(__file__).resolve().parents[1] / "docs" / "DESIGN.md").read_text()
    line = next(x for x in design.splitlines() if x.startswith("Codes: "))
    documented = set(re.findall(r"`([a-z_]+)`", line.split(". ", 1)[0]))  # the list ends at the first sentence
    schema = set(re.findall(r"[a-z]+(?:_[a-z]+)+|\b(?:timeout|cancelled)\b", ErrorInfo.model_fields["code"].description.split(":", 1)[1]))
    assert raised - documented == set(), f"raised but missing from docs/DESIGN.md: {sorted(raised - documented)}"
    assert raised - schema == set(), f"raised but missing from ErrorInfo.code: {sorted(raised - schema)}"
    assert documented == schema, (sorted(documented ^ schema))


@pytest.mark.parametrize("line,ok", [
    ("3 passed in 1.0s", True),
    ("2 passed, 18 skipped in 1.1s", True),
    ("18 skipped in 1.2s", False),
    ("1 failed, 2 passed in 1.0s", False),
    ("1 passed, 1 error in 1.0s", False),
    ("no tests ran in 0.01s", False),
    ("", False),
])
def test_live_tests_summary_needs_a_pass(line: str, ok: bool):
    """A suite that only skipped (no SDP, no target) must not count as a passed live run."""
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / "live-tests.sh"
    r = subprocess.run(["sh", str(script), "--summary-ok", line], env={"PATH": os.environ["PATH"]}, capture_output=True, timeout=10)
    assert (r.returncode == 0) is ok, (line, r.returncode, r.stderr)


@pytest.mark.parametrize("tool", ["deploy_and_run", "run_start"])
@pytest.mark.parametrize("args,env,field", [
    (["ok", "a\0b"], None, "args[1]"),
    (["x" * 40000, "y" * 40000], None, "args"),
    ([], {"BIG": "v" * 70000}, "env"),
], ids=["nul", "args-too-large", "env-too-large"])
def test_run_args_are_checked_before_upload(tmp_path: Path, monkeypatch, tool, args, env, field):
    """A NUL used to reach Popen as internal_error after the upload; now it is refused before anything happens."""
    from qnx_mcp import ops
    d = tmp_path / "targets" / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\n")
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "s"),
                           "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path)})

    def never(*a, **kw):
        raise AssertionError("nothing may be snapshotted, uploaded or started")

    monkeypatch.setattr(ops, "_program", never)
    monkeypatch.setattr(Target, "require_ip", never)
    with pytest.raises(QnxMcpError) as e:
        getattr(ops, tool)(cfg, "tb1", remote_binary="/data/x", args=args, env=env)
    assert e.value.code == "invalid_argument" and field in e.value.message, e.value.to_dict()


@pytest.mark.parametrize("name", ["state dir", "state\tdir", "state%h", "state$HOME", 'state"q'])
def test_state_dir_that_ssh_would_split_is_refused(tmp_path: Path, name: str):
    """ssh splits UserKnownHostsFile at whitespace and expands % tokens and variables, so the pin would be lost."""
    from qnx_mcp.ops import environment_check
    d = tmp_path / "targets" / "tb1" / "local" / "misc_files"
    d.mkdir(parents=True)
    (d.parent / "options").write_text("OPT_ARCH='x86_64'\n")
    (d / "ssh_host_ed25519_key.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbc root@qnx\n")

    def cfg_for(state: Path) -> Config:
        return Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(state), "QNX_MCP_ALLOW_TARGETS": "tb*",
                                "QNX_MCP_WORKSPACE": str(tmp_path), "QNX_MCP_SDP_ENV": str(tmp_path / "none")})

    bad = cfg_for(tmp_path / name)
    with pytest.raises(QnxMcpError) as e:
        Target(bad, "tb1")._ssh_opts()
    assert e.value.code == "internal_error" and "QNX_MCP_STATE_DIR" in (e.value.hint or ""), e.value.to_dict()
    check = {c["check"]: c for c in environment_check(bad)["checks"]}["state_dir"]
    assert not check["ok"] and "QNX_MCP_STATE_DIR" in check["fix"]
    good = cfg_for(tmp_path / "state")
    assert f"UserKnownHostsFile={good.state_dir / 'targets' / 'tb1' / 'known_hosts'}" in Target(good, "tb1")._ssh_opts()
    assert {c["check"]: c for c in environment_check(good)["checks"]}["state_dir"]["ok"]


def test_qcc_inputs_are_checked_by_content(tmp_path: Path):
    """GNU ld reads an input it does not recognise as a linker script, so a name ending in .o, .a or .so is not enough."""
    from helpers import write_elf
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    for name in ("x.o", "libx.a", "libx.so"):
        (proj / name).write_text("INPUT(/etc/hosts)\n")
    (proj / "thin.a").write_bytes(b"!<thin>\n")
    for arg in ("x.o", "libx.a", "libx.so", str(proj / "x.o"), "missing.o", "thin.a"):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(proj), system="qcc", ldflags=[arg])
        assert e.value.code == "invalid_argument" and repr(arg) in e.value.message and "ldflags" in e.value.message, e.value.to_dict()
    assert _recorded(log) == []
    write_elf(proj / "ok.o")
    write_elf(proj / "libok.so")
    (proj / "libok.a").write_bytes(b"!<arch>\n")
    assert build(cfg, str(proj), system="qcc", ldflags=["ok.o", "libok.a", str(proj / "libok.so")])["ok"]
    assert _recorded(log)[0][-3:] == ["ok.o", "libok.a", str(proj / "libok.so")]


def test_qcc_sources_need_a_source_extension(tmp_path: Path):
    """gcc hands a file with an unknown extension to the linker, which reads it as a linker script."""
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    for name in ("x.ld", "x.txt", "x", "x.h"):
        (proj / name).write_text("INPUT(/etc/hosts)\n")
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(proj), system="qcc", sources=[name])
        assert e.value.code == "invalid_argument" and "sources" in e.value.message and repr(name) in e.value.message, e.value.to_dict()
    with pytest.raises(QnxMcpError) as e:
        build(cfg, str(proj / "x.txt"))  # a file as path becomes the first source
    assert e.value.code == "invalid_argument" and "sources" in e.value.message
    assert _recorded(log) == []
    for name in ("a.c", "b.cpp", "c.cc", "d.cxx", "e.s", "f.S"):
        (proj / name).write_text("\n")
    assert build(cfg, str(proj), system="qcc", sources=["a.c", "b.cpp", "c.cc", "d.cxx", "e.s", "f.S"])["ok"]


def test_qcc_library_search_is_checked_by_content(tmp_path: Path):
    """ld opens libNAME.so or libNAME.a in each -L directory for -lNAME and reads a text file as a linker script."""
    from helpers import write_elf
    from qnx_mcp.sdk import build
    cfg, ws, log = _fake_sdp(tmp_path)
    proj = ws / "proj"
    (proj / "lib").mkdir()
    (proj / "lib" / "libtext.so").write_text("INPUT(/etc/hosts)\n")
    (proj / "lib" / "libtext2.a").write_text("INPUT(/etc/hosts)\n")
    (proj / "lib" / "libthin.a").write_bytes(b"!<thin>\n")
    write_elf(tmp_path / "outside.so")
    (proj / "lib" / "liblink.so").symlink_to(tmp_path / "outside.so")
    for cflags, ldflags in ((None, ["-Llib", "-ltext"]), (None, ["-L", "lib", "-ltext"]), (None, ["-L" + str(proj / "lib"), "-ltext2"]),
                            (None, ["-Llib", "-lthin"]), (["-ltext"], ["-Llib"]), (None, ["-Llib", "-llink"])):
        with pytest.raises(QnxMcpError) as e:
            build(cfg, str(proj), system="qcc", cflags=cflags, ldflags=ldflags)
        assert e.value.code in ("invalid_argument", "path_not_allowed") and "ldflags" in e.value.message, e.value.to_dict()
    assert _recorded(log) == []
    write_elf(proj / "lib" / "libok.so")
    (proj / "lib" / "libok2.a").write_bytes(b"!<arch>\n")
    assert build(cfg, str(proj), system="qcc", ldflags=["-Llib", "-lok", "-lok2", "-lm"])["ok"]
    assert _recorded(log)[0][-4:] == ["-Llib", "-lok", "-lok2", "-lm"]


@pytest.mark.parametrize("name", ["key%%", "key%h", "key$HOME", "key${HOME}"])
def test_ssh_key_that_ssh_would_expand_is_refused(tmp_path: Path, name: str):
    """ssh expands % tokens and variables in the -i file name, so it would open another file than the one configured."""
    from qnx_mcp.ops import environment_check
    d = tmp_path / "targets" / "tb1" / "local" / "misc_files"
    d.mkdir(parents=True)
    (d.parent / "options").write_text("OPT_ARCH='x86_64'\n")
    (d / "ssh_host_ed25519_key.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbc root@qnx\n")

    def cfg_for(key: Path) -> Config:
        return Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "state"),
                                "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path), "QNX_MCP_SDP_ENV": str(tmp_path / "none"),
                                "QNX_MCP_SSH_KEY": str(key)})

    bad = cfg_for(tmp_path / name)
    Path(str(bad.ssh_key) + ".pub").write_text("ssh-ed25519 AAAA x\n")
    with pytest.raises(QnxMcpError) as e:
        Target(bad, "tb1")._ssh_opts()
    assert e.value.code == "internal_error" and "QNX_MCP_SSH_KEY" in (e.value.hint or ""), e.value.to_dict()
    check = {c["check"]: c for c in environment_check(bad)["checks"]}["ssh_key"]
    assert not check["ok"] and "QNX_MCP_SSH_KEY" in check["fix"]
    good = cfg_for(tmp_path / "key dir" / "id_ed25519")  # whitespace is fine: -i is one argument and is not split
    (tmp_path / "key dir").mkdir()
    Path(str(good.ssh_key) + ".pub").write_text("ssh-ed25519 AAAA x\n")
    assert ["-i", str(good.ssh_key)] == Target(good, "tb1")._ssh_opts()[-2:]
    assert {c["check"]: c for c in environment_check(good)["checks"]}["ssh_key"]["ok"]


def test_upload_removes_a_partial_copy_after_a_failed_or_cancelled_copy(fake_target):
    """scp can leave part of the file at the temporary name; it is removed even when the call was cancelled."""
    from qnx_mcp import util
    from qnx_mcp.ops import _upload
    t, _, app = fake_target
    sha, size = _sha(app), app.stat().st_size
    ctx = util.CallContext()
    ctx.cancelled = True
    fake = FakeRemote(t, [QnxMcpError("cancelled", "the client cancelled this call"), _reply()])
    ssh = fake.ssh

    def ssh_like_run(argv, **kw):
        util.check_cancelled()  # the real ssh refuses to start inside a cancelled call
        return ssh(argv, **kw)

    t.ssh = ssh_like_run
    token = util.CURRENT_CALL.set(ctx)
    try:
        with pytest.raises(QnxMcpError) as e:
            _upload(t, "10.0.0.1", app, "/tmp/qnx-mcp/apps/x/app", sha, size, "0755", "/tmp/qnx-mcp/bin/r")
    finally:
        util.CURRENT_CALL.reset(token)
    assert e.value.code == "cancelled"
    (k1, (_, tmp)), (k2, rm) = fake.calls
    assert k1 == "put" and k2 == "ssh" and rm == ["rm", "-f", tmp] and tmp.startswith("/tmp/qnx-mcp/apps/x/app.part-")


def test_missing_core_build_id_message_does_not_blame_the_core():
    """The core's build ID is read at the address of the given binary's note, so a wrong binary finds none there
    either; seen on a target with a core that does hold a build ID. The message must not say the core has none."""
    from qnx_mcp import crash
    with pytest.raises(QnxMcpError) as e:
        crash._provenance(Path("core"), Path("bin"), {"build_id": "ab" * 10}, "b" * 64, None, _mi(), _CORE_INFO)
    assert e.value.code == "core_mismatch" and "not the one that crashed" in e.value.message, e.value.to_dict()
    rec = {**_RUN_REC, "remote_binary": True, "remote_path": "/tmp/qnx-mcp/apps/abc/crash"}
    ev = crash._provenance(Path("core"), Path("bin"), {"build_id": "ab" * 10}, "b" * 64, rec, _mi(), _CORE_INFO)
    assert ev["verified"] is False and "may not be the one that ran" in ev["evidence"], ev


def test_run_poll_backoff_opens_fewer_sessions(tmp_path: Path, monkeypatch):
    """Each poll is a new ssh session; a long wait backs off to one every 2 s and still ends at its deadline."""
    from qnx_mcp import ops
    d = tmp_path / "targets" / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\n")
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "s"),
                           "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path)})
    clock = [1000.0]
    sleeps: list[float] = []
    polls: list[float] = []
    new_output_at = [None]

    def poll_once(t, ip, rec, out_off, err_off, max_bytes):
        polls.append(clock[0])
        nxt = 5 if new_output_at[0] is not None and clock[0] >= new_output_at[0] else 0
        return {"alive": True, "status": None, "stdout": {"next": nxt, "size": nxt}, "stderr": {"next": 0, "size": 0}}

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(ops, "_poll_once", poll_once)
    monkeypatch.setattr(ops.time, "sleep", sleep)
    monkeypatch.setattr(ops.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ops, "_owned_background", lambda t, rid: {"run_id": rid, "state": "running", "pid": 7})
    monkeypatch.setattr(Target, "require_ip", lambda self: "10.0.0.1")
    r = ops.run_poll(cfg, "tb1", "r-000000000001", wait_s=60)
    assert r["state"] == "running" and r["stdout"] == ""
    assert sleeps[:4] == [0.5, 1.0, 2.0, 2.0] and max(sleeps) <= 2.0
    assert len(polls) <= 35 and clock[0] - 1000.0 <= 60.0 + 1e-9 and polls[-1] - 1000.0 >= 58.0, (len(polls), clock[0])
    polls.clear()
    sleeps.clear()
    new_output_at[0] = clock[0] + 3.0
    r = ops.run_poll(cfg, "tb1", "r-000000000001", wait_s=60)
    assert r["stdout_next"] == 5 and len(polls) == 4  # 0, 0.5, 1.5, 3.5: stops at the first poll that sees output


def test_remote_dirs_are_private_or_refused(tmp_path: Path):
    """The fragment prepare sends to the target, run here with the local sh: directories end up with their mode and
    owned by the user running it; a link, a file or (on the target) another user's directory stops it with exit 4."""
    import subprocess
    from qnx_mcp.ops import _secure_dirs_script
    root = tmp_path / "qnx-mcp"

    def sh(dirs: list[tuple[Path, str]]) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", "-c", _secure_dirs_script([(str(d), m) for d, m in dirs])], capture_output=True, text=True, timeout=10)

    tree = [(root, "0711"), (root / "apps", "0711"), (root / "apps" / "0123456789abcdef", "0711"), (root / "bin", "0700"),
            (root / "runs", "0711"), (root / "runs" / "r-000000000001", "0700")]
    assert sh(tree).returncode == 0
    assert all(d.is_dir() and stat.S_IMODE(d.stat().st_mode) == int(m, 8) for d, m in tree)
    (root / "runs").chmod(0o777)
    assert sh(tree).returncode == 0 and stat.S_IMODE((root / "runs").stat().st_mode) == 0o711
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o755)
    linked = tmp_path / "linked"
    linked.symlink_to(elsewhere)
    r = sh([(linked, "0711"), (linked / "bin", "0700")])
    assert r.returncode == 4 and str(linked) in r.stderr and stat.S_IMODE(elsewhere.stat().st_mode) == 0o755 and not (elsewhere / "bin").exists()
    (tmp_path / "plain").write_text("x")
    assert sh([(tmp_path / "plain", "0711")]).returncode == 4
    assert "test -O " in _secure_dirs_script([("/tmp/qnx-mcp", "0711")])  # another owner is refused (needs root to test here)


def _linkmap_core(path: Path, exe: str, exe_bid: bytes, prev: int = 0) -> Path:
    """A minimal ELF64 core with one PT_NOTE holding a QNX QNT_LINK_MAP note laid out as a QNX 8.0 dumper writes it
    (sys/qnx_linkmap_note.h): header, r_debug and link_maps, string table, then the build ID table at a 4-byte boundary."""
    strtab = b"PIE\0" + exe.encode() + b"\0libc.so.6\0/proc/boot/\0"
    lib_name, lib_path = 5 + len(exe), 15 + len(exe)
    r_debug = struct.pack("<iiQQIIQQII", 1, 0, 0x1000, 0, 0, 0, 0, 0, 0, 0)
    maps = struct.pack("<8Q", 0x400000, 0, 0, 0x2000, prev, 0, 0, 4) + struct.pack("<8Q", 0x800000, lib_name, 0, 0, 0x1000, 0, 0, lib_path)
    lm = r_debug + maps
    bids = b"".join(struct.pack("<HH", 0, len(b)) + b + b"\0" * (-len(b) % 4) for b in (exe_bid, bytes(range(16))))
    pad = b"\0" * (-(32 + len(lm) + len(strtab)) % 4)
    desc = struct.pack("<IIII16x", 1, len(lm), len(strtab), len(bids)) + lm + strtab + pad + bids
    note = struct.pack("<III", 4, len(desc), 11) + b"QNX\0" + desc + b"\0" * (-len(desc) % 4)
    phoff, noteoff = 64, 64 + 56
    ehdr = b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", 4, 62, 1, 0, phoff, 0, 0, 64, 56, 1, 0, 0, 0)
    phdr = struct.pack("<IIQQQQQQ", 4, 0, noteoff, 0, 0, len(note), 0, 0)
    path.write_bytes(ehdr + phdr + note)
    return path


def test_core_link_map_names_the_build_id_that_ran(tmp_path: Path):
    from qnx_mcp.parsers import core_exe_build_id
    core = _linkmap_core(tmp_path / "c.core", "/data/opt/crash", bytes.fromhex("aa" * 16))
    assert core_exe_build_id(str(core), "/data/opt/crash") == {"build_id": "aa" * 16, "path": "/data/opt/crash"}
    assert core_exe_build_id(str(core), "/data/opt/other") is None  # not the executable gdb names: undecided
    assert core_exe_build_id(str(_linkmap_core(tmp_path / "n.core", "/data/opt/crash", b"")), "/data/opt/crash")["build_id"] is None
    assert core_exe_build_id(str(_linkmap_core(tmp_path / "p.core", "/data/opt/crash", b"\xaa" * 16, prev=8)), "/data/opt/crash") is None
    (tmp_path / "short.core").write_bytes(core.read_bytes()[:150])
    assert core_exe_build_id(str(tmp_path / "short.core"), "/data/opt/crash") is None


@pytest.mark.parametrize("core_bid,bin_bid,expected", [
    ("aa" * 16, "bb" * 16, "mismatch"),  # another program: before, verified false with a ?? backtrace
    ("aa" * 16, None, "mismatch"),  # the binary has no build ID, the program that ran had one
    ("", "aa" * 16, "mismatch"),  # the program that ran had none, the binary has one
    ("aa" * 16, "aa" * 16, (True, "build ID, executable path and pid match")),
    ("", None, (False, "pid and path match; build ID not available (neither the program that ran nor this binary has one)")),
], ids=["other-build-id", "binary-without-id", "core-without-id", "same", "neither"])
def test_remote_run_build_id_comes_from_the_core_link_map(tmp_path: Path, monkeypatch, core_bid, bin_bid, expected):
    """A wrong host binary used to leave a remote_binary run unverified with useless frames; the dumper's link map
    note records the executable's build ID, so a different one is a core_mismatch."""
    from qnx_mcp import crash
    monkeypatch.setattr(crash, "core_read", lambda *a: None)  # a wrong binary finds nothing at its own note address
    core = _linkmap_core(tmp_path / "c.core", "/data/opt/crash", bytes.fromhex(core_bid))
    args = (core, Path("bin"), {"build_id": bin_bid} if bin_bid else {}, "b" * 64, _REMOTE_REC, _mi(exe="/data/opt/crash", note=True), _CORE_INFO)
    if expected == "mismatch":
        with pytest.raises(QnxMcpError) as e:
            crash._provenance(*args)
        assert e.value.code == "core_mismatch" and e.value.details["provenance"]["core_build_id_source"] == "link_map", e.value.to_dict()
        return
    ev = crash._provenance(*args)
    assert ev["verified"] is expected[0] and ev["evidence"] == expected[1], ev


@pytest.mark.parametrize("name", ["state dir", "state%h"])
def test_target_status_refuses_an_unsafe_state_dir_like_other_tools(tmp_path: Path, monkeypatch, name: str):
    """target_status used to return ok:true with the refusal in its ssh field; every other tool fails the call."""
    d = tmp_path / "targets" / "tb1" / "local" / "misc_files"
    d.mkdir(parents=True)
    (d.parent / "options").write_text("OPT_ARCH='x86_64'\n")
    (d / "ssh_host_ed25519_key.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbc root@qnx\n")
    cfg = Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / name),
                           "QNX_MCP_ALLOW_TARGETS": "tb*", "QNX_MCP_WORKSPACE": str(tmp_path)})
    monkeypatch.setattr(Target, "qemu_pid", lambda self: 4242)
    monkeypatch.setattr(Target, "lookup_ip", lambda self: "10.0.0.1")
    with pytest.raises(QnxMcpError) as e:
        Target(cfg, "tb1").status(probe=True)
    assert e.value.code == "internal_error" and "QNX_MCP_STATE_DIR" in (e.value.hint or ""), e.value.to_dict()


def test_remote_dir_modes_let_a_non_root_program_reach_only_its_own_paths(fake_target):
    """With every directory 0700 (as merged from imp-b1) a run as uid 1000 ended exec_failed: Permission denied on a
    QNX 8.0 target. The program needs search access on the path to its executable and to its run's work directory;
    the runner's directory and new run directories stay 0700. The modes do not depend on QNX_MCP_RUN_AS, so a root
    run after a non-root run (or the other way round) sets the same modes."""
    from qnx_mcp.ops import prepare
    t, runner, app = fake_target
    entry = _app_entry(app)
    lines = [_runner_json(sha256=_sha(runner), size=runner.stat().st_size, mode="0755"), _runner_json(sha256=entry[2], size=entry[3], mode="0755")]
    fake = FakeRemote(t, [_reply("\n".join(lines) + "\n")])
    prepare(t, "10.0.0.1", [entry], ["/tmp/qnx-mcp/runs/r-000000000001"])
    script = fake.calls[0][1]
    for d, mode in (("/tmp/qnx-mcp", "0711"), ("/tmp/qnx-mcp/apps", "0711"), ("/tmp/qnx-mcp/apps/0123456789abcdef", "0711"),
                    ("/tmp/qnx-mcp/runs", "0711"), ("/tmp/qnx-mcp/bin", "0700"), ("/tmp/qnx-mcp/runs/r-000000000001", "0700")):
        assert f"mkdir -m {mode} {d};" in script and f"chmod {mode} {d};" in script, (d, mode)


def test_provenance_says_when_the_run_user_could_reach_the_core():
    """dumper writes the core of a user with a home directory into that home, which the user owns, so a process of
    that user could change it before the runner takes it; the runner reports that and get_crash passes it on."""
    from qnx_mcp import crash
    rec = {**_RUN_REC, "core_exposed": "dumper wrote it in /data/home/qnxuser/cores, which belongs to the program's user"}
    ev = crash._provenance(Path("core"), Path("bin"), {}, "b" * 64, rec, _mi(), _CORE_INFO)
    assert ev["verified"] is True and ev["core_exposed"] == rec["core_exposed"], ev
    assert ev["evidence"].startswith("executable path and pid match; the run's user could reach the core") and "/data/home/qnxuser" in ev["evidence"]
    assert crash._provenance(Path("core"), Path("bin"), {}, "b" * 64, _RUN_REC, _mi(), _CORE_INFO)["core_exposed"] is None
