# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Offline unit tests: parsers, validation, quoting, timeouts and locking. No QNX target needed."""

from __future__ import annotations

import shlex
import threading
import time
from pathlib import Path

import pytest

from qnx_mcp.parsers import elf_info, parse_diagnostics, parse_mi_output, parse_pidin, parse_slog2
from qnx_mcp.target import validate_mkqnximage_options
from qnx_mcp.util import Config, QnxMcpError, check_remote_path, check_target_name, file_lock, quote_argv, run

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
    assert e[0]["process"] == "io_usb_otg" and e[0]["pid"] == 282643 and e[0]["severity_name"] == "warning"
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
