# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Offline unit tests: parsers, validation, quoting, timeouts and locking. No QNX target needed."""

from __future__ import annotations

import json
import os
import shlex
import stat
import threading
import time
from pathlib import Path

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


def test_elf_info_on_host_binary():
    info = elf_info("/bin/ls")
    assert info and info["arch"] == "x86_64" and info["qnx"] is False
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
