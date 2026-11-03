# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""get_crash: fetch a core with verified provenance and turn it into a structured backtrace (SDP gdb, MI)."""

from __future__ import annotations

import contextlib
import gzip
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .ops import _remote_hash, _sha256_file, all_records, load_record, prepare, runs_dir
from .parsers import core_note_bytes, elf_info, parse_mi_output
from .sdk import sdk_env, sdk_tool
from .target import Target, gdb_for
from .util import Config, QnxMcpError, check_host_path, check_remote_path, private_dir, run, tail, within

MAX_THREADS = 64
THREAD_FRAMES = 16
MAX_SOURCE = 4 << 20
SOURCE_EXT = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".inl", ".s", ".S")
_SEGV_CODES = {1: "SEGV_MAPERR (address not mapped)", 2: "SEGV_ACCERR (no permission)", 3: "SEGV_STKERR", 4: "SEGV_GPERR (general protection)", 5: "SEGV_IRQERR"}


def _source_context(cfg: Config, path: str | None, line: int | None, radius: int = 3) -> list[dict[str, Any]] | None:
    """Source lines around a frame. Paths come from DWARF in the binary, so only C/C++ sources are
    read, and only inside QNX_MCP_WORKSPACE when it is set."""
    if not path or not line or not path.endswith(SOURCE_EXT):
        return None
    real = os.path.realpath(path)
    if not os.path.isfile(real) or os.path.getsize(real) > MAX_SOURCE or (cfg.workspace and not within(Path(real), cfg.workspace)):
        return None
    try:
        lines = Path(real).read_text(errors="replace").splitlines()
    except OSError:
        return None
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return [{"line": n, "text": lines[n - 1], "current": n == line} for n in range(lo, hi + 1)]


def _frame(f: dict[str, Any], sdk_target: str) -> dict[str, Any]:
    if not isinstance(f, dict):
        raise QnxMcpError("gdb_failed", "malformed frame record from gdb")
    fullname = f.get("fullname")
    return {
        "level": int(f["level"]) if str(f.get("level", "")).isdigit() else None,
        "function": f.get("func"),
        "file": f.get("file"),
        "fullname": fullname,
        "line": int(f["line"]) if str(f.get("line", "")).isdigit() else None,
        "address": f.get("addr"),
        "library": f.get("from"),
        "user_code": bool(fullname and os.path.isfile(fullname) and not fullname.startswith(sdk_target)),
    }


def _frames(rec: dict[str, Any], sdk_target: str) -> list[dict[str, Any]]:
    stack = rec.get("stack")
    if not isinstance(stack, list) or any(not isinstance(i, dict) or list(i) != ["frame"] for i in stack):
        raise QnxMcpError("gdb_failed", "malformed stack record from gdb")
    return [_frame(i["frame"], sdk_target) for i in stack]


def _gunzip(src: Path, limit: int) -> Path:
    dst = src.with_suffix("")
    total = 0
    with gzip.open(src, "rb") as f, open(dst, "wb") as out:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                out.close()
                dst.unlink()
                raise QnxMcpError("core_too_large", f"decompressed core exceeds {limit >> 20} MiB", hint="Raise max_core_mb (up to 4096).")
            out.write(chunk)
    return dst


def _latest_crash(t: Target) -> dict[str, Any] | None:
    recs = [r for r in all_records(t) if r.get("core_path")]
    return recs[-1] if recs else None


def get_crash(
    cfg: Config,
    target: str,
    run_id: str | None = None,
    core_path: str | None = None,
    binary: str | None = None,
    max_frames: int = 32,
    locals_frames: int = 3,
    max_core_mb: int = 512,
) -> dict[str, Any]:
    t = Target(cfg, target)
    if not 1 <= max_frames <= 256 or not 0 <= locals_frames <= 16 or not 1 <= max_core_mb <= 4096:
        raise QnxMcpError("invalid_argument", "max_frames must be 1-256, locals_frames 0-16, max_core_mb 1-4096")
    if core_path or binary:
        if run_id or not (core_path and binary):
            raise QnxMcpError("invalid_argument", "pass either run_id, or both core_path and binary", hint="Omit everything to analyse the newest crashed run.")
        remote_core = check_remote_path(core_path, "core_path")
        local_bin = check_host_path(cfg, binary, "binary")
        rec = None
        dl_dir = private_dir(cfg.state_dir / "cores" / target / f"manual-{uuid.uuid4().hex[:12]}")
    else:
        rec = load_record(t, run_id) if run_id else _latest_crash(t)
        if run_id and rec is None:
            raise QnxMcpError("invalid_argument", f"unknown run_id {run_id!r} for target {target}", hint="Use a run_id from deploy_and_run or run_start on this target.")
        if rec is None or not rec.get("core_path"):
            raise QnxMcpError("core_not_found", "no crashed run with a core file is recorded" + (f" for {run_id}" if run_id else " for this target"),
                              hint="Run the program with deploy_and_run first, or pass core_path and binary explicitly.")
        remote_core = check_remote_path(rec["core_path"], "core_path")
        local_bin = Path(rec["binary_snapshot"])
        if not local_bin.is_file():
            raise QnxMcpError("core_not_found", f"the binary snapshot of run {rec['run_id']} was pruned", hint="Rerun the program to get a fresh crash.")
        dl_dir = private_dir(runs_dir(t) / rec["run_id"] / "cores" / uuid.uuid4().hex[:12])

    limit = max_core_mb << 20
    with t.lock(exclusive=False):
        ip = t.require_ip()
        runner = prepare(t, ip, [], [])
        info = _remote_hash(t, ip, runner, remote_core)
        if info is None:
            raise QnxMcpError("core_not_found", f"{remote_core} no longer exists on {target}", hint="Cores live under /tmp/qnx-mcp/runs (newest 20 runs); rerun the program.")
        if info["size"] > limit:
            raise QnxMcpError("core_too_large", f"{remote_core} is {info['size']} bytes, above max_core_mb={max_core_mb}", hint="Raise max_core_mb (up to 4096).")
        core = dl_dir / os.path.basename(remote_core)
        part = dl_dir / (core.name + ".part")
        t.get(remote_core, part, timeout=600, ip=ip)
        if part.stat().st_size != info["size"] or _sha256_file(part) != info["sha256"]:
            raise QnxMcpError("copy_failed", f"downloaded core does not match {remote_core} on the target")
        os.replace(part, core)
    if core.suffix == ".gz":
        core = _gunzip(core, limit)
    core_info = elf_info(str(core))
    if core_info is None or core_info["type"] != "core":
        raise QnxMcpError("core_not_found", f"{remote_core} is not an ELF core file")

    # Provenance: the core must name this exact binary (its GNU build ID, or for qnx-mcp runs the
    # content-addressed path it ran from, which embeds the binary's SHA-256 prefix).
    notes = core_note_bytes(str(core)) or b""
    bin_info = elf_info(str(local_bin)) or {}
    build_id = bin_info.get("build_id")
    build_id_in_core = (bytes.fromhex(build_id) in notes) if build_id else None
    path_in_core = (rec["remote_path"].encode() in notes) if rec else None
    if not build_id_in_core and not path_in_core and (build_id_in_core is False or path_in_core is False):
        raise QnxMcpError(
            "core_mismatch",
            f"the core {remote_core} was not produced by {local_bin.name if rec is None else 'the binary deployed in ' + rec['run_id']}",
            hint="Pass the exact unstripped binary that crashed (same build), or use the run_id of the run that crashed.",
            binary_build_id=build_id,
        )
    provenance = {
        "source": "run_record" if rec else "manual",
        "binary_sha256": _sha256_file(local_bin),
        "binary_build_id": build_id,
        "build_id_in_core": build_id_in_core,
        "deployed_path_in_core": path_in_core,
        "core_sha256": info["sha256"],
        "core_size": info["size"],
        "verified": bool(build_id_in_core or path_in_core),
    }

    env = sdk_env(cfg)
    arch = t.arch
    sysroot = os.path.join(env["QNX_TARGET"], arch)
    libpath = ":".join(os.path.join(sysroot, p) for p in ("lib", "usr/lib", "lib/dll"))
    cmds = [
        "1-thread-info",
        "2-data-evaluate-expression $_siginfo.si_signo",
        "3-data-evaluate-expression $_siginfo.si_code",
        "4-data-evaluate-expression $_siginfo.__data.__fault.__addr",
        '5-interpreter-exec console "x/i $pc"',
        f"6-stack-list-frames 0 {max_frames - 1}",
    ]
    for i in range(locals_frames):
        cmds += [f"{100 + i}-stack-select-frame {i}", f"{200 + i}-stack-list-variables --simple-values"]
    for n in range(1, MAX_THREADS + 1):
        cmds += [f"{3000 + n}-thread-select {n}", f"{4000 + n}-stack-list-frames 0 {THREAD_FRAMES - 1}"]
    cmds.append("-gdb-exit")
    argv = [
        sdk_tool(env, gdb_for(arch)), "-q", "-nx", "--interpreter=mi2",
        "-iex", f"set sysroot {sysroot}",
        "-iex", f"set solib-search-path {libpath}",
        "-iex", "set auto-load off",
        "-iex", "set pagination off",
        str(local_bin), str(core),
    ]
    r = run(argv, timeout=120, env=env, input=("\n".join(cmds) + "\n").encode(), max_output=32 << 20)
    if r.timed_out:
        raise QnxMcpError("timeout", "gdb did not finish within 120s")
    if r.truncated:
        raise QnxMcpError("gdb_failed", "gdb output exceeded 32 MiB", hint="Lower max_frames.")
    mi = parse_mi_output(r.out)
    res = mi["results"]

    def required(tok: str, what: str) -> dict[str, Any]:
        rec_ = res.get(tok)
        if rec_ is None or rec_.get("class") != "done" or rec_.get("malformed") or tok in mi["duplicates"]:
            raise QnxMcpError("gdb_failed", f"gdb could not {what}", gdb_error=(rec_ or {}).get("msg") or (rec_ or {}).get("parse_error"),
                              stderr=tail(r.err, 800), log=tail("".join(mi["log"]), 800))
        return rec_

    def value(tok: str) -> str | None:
        rec_ = res.get(tok) or {}
        return rec_.get("value") if rec_.get("class") == "done" and not rec_.get("malformed") else None

    sdk_target = env["QNX_TARGET"]
    frames = _frames(required("6", "read a stack from the core"), sdk_target)
    if not frames:
        raise QnxMcpError("gdb_failed", "gdb returned an empty stack for the faulting thread")
    threads_rec = required("1", "list the threads in the core")
    for f in frames:
        f["source"] = _source_context(cfg, f["fullname"], f["line"]) if f["user_code"] else None
    for i in range(min(locals_frames, len(frames))):
        sel, var = res.get(str(100 + i)) or {}, res.get(str(200 + i)) or {}
        if sel.get("class") == "done" and var.get("class") == "done" and isinstance(var.get("variables"), list):
            frames[i]["locals"] = [{"name": v.get("name"), "type": v.get("type"), "value": v.get("value"), "arg": v.get("arg") == "1"}
                                   for v in var["variables"] if isinstance(v, dict)]

    # The process the core belongs to, from gdb's thread ids ("pid 798741 tid 1"): a run's core must be its own process.
    pids = {int(m_.group(1)) for th in threads_rec.get("threads", []) if isinstance(th, dict)
            for m_ in [re.search(r"\bpid (\d+)", str(th.get("target-id", "")))] if m_}
    core_pid = next(iter(pids)) if len(pids) == 1 else None
    provenance["core_pid"] = core_pid
    if rec is not None and rec.get("pid") is not None:
        provenance["pid_matches_run"] = core_pid == rec["pid"]
        if core_pid != rec["pid"]:
            raise QnxMcpError("core_mismatch", f"the core belongs to pid {core_pid}, but run {rec['run_id']} was pid {rec['pid']}",
                              hint="Another process of the same program overwrote the core; rerun the program.", provenance=provenance)
    current = threads_rec.get("current-thread-id")
    threads = []
    for th in threads_rec.get("threads", []) if isinstance(threads_rec.get("threads"), list) else []:
        if not isinstance(th, dict):
            continue
        entry: dict[str, Any] = {"id": th.get("id"), "target_id": th.get("target-id"), "name": th.get("name"), "state": th.get("state"),
                                 "faulting": th.get("id") == current}
        n = int(th["id"]) if str(th.get("id", "")).isdigit() else 0
        sel, st = res.get(str(3000 + n)) or {}, res.get(str(4000 + n)) or {}
        if 0 < n <= MAX_THREADS and sel.get("class") == "done" and st.get("class") == "done" and not st.get("malformed"):
            entry["frames"] = [{k: f[k] for k in ("level", "function", "file", "line", "address", "library")} for f in _frames(st, sdk_target)]
        threads.append(entry)

    console = "".join(mi["console"])
    m = re.search(r"Program terminated with signal (\w+), ([^.\n]+)", console)
    signo, code, fault = value("2"), value("3"), value("4")
    fault_addr = fault.split()[0] if fault else None
    insn = next((c.strip() for c in mi["console"] if c.lstrip().startswith("=>")), None)
    sig_name = m.group(1) if m else (rec or {}).get("signal_name")
    si_code = int(code) if code and code.lstrip("-").isdigit() else None
    code_name = _SEGV_CODES.get(si_code) if sig_name == "SIGSEGV" and si_code is not None else None
    fault_frame = frames[0]
    user = next((f for f in frames if f["user_code"]), None)

    def where(f: dict[str, Any]) -> str:
        if f.get("file") and f.get("line"):
            return f"{f['function']} at {f['file']}:{f['line']}"
        return f"{f['function'] or f['address']}" + (f" ({os.path.basename(f['library'])})" if f.get("library") else "")

    summary = (sig_name or "signal") + (f" ({code_name})" if code_name else "") + (f" at fault address {fault_addr}" if fault_addr else "")
    summary += f" in {where(fault_frame)}"
    if user is not None and user is not fault_frame:
        summary += f", called from {where(user)}"
    summary += "; stack: " + " <- ".join(f"{f['function']}()" for f in frames[:6] if f["function"])
    if fault_addr in ("0x0", "0") and sig_name == "SIGSEGV":
        summary += ". Fault address 0x0 means a NULL pointer dereference."
    warnings = [w.strip() for w in mi["log"] if w.lstrip().startswith("warning:")][:20]
    with contextlib.suppress(OSError):
        os.chmod(core, 0o600)
    return {
        "ok": True,
        "target": target,
        "run_id": (rec or {}).get("run_id"),
        "summary": summary,
        "signal": int(signo) if signo and signo.isdigit() else None,
        "signal_name": sig_name,
        "signal_description": m.group(2) if m else None,
        "si_code": si_code,
        "si_code_name": code_name,
        "fault_address": fault_addr,
        "faulting_instruction": insn,
        "faulting_thread_id": current,
        "faulting_frame": fault_frame,
        "user_frame": user,
        "fault_in_user_code": user is fault_frame,
        "frames": frames,
        "threads": threads,
        "provenance": provenance,
        "core_path_target": remote_core,
        "core_path_local": str(core),
        "binary": str(local_bin),
        "binary_original": (rec or {}).get("binary", str(local_bin)),
        "gdb_warnings": warnings,
    }
