# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Target operations: deploy and run, crash analysis, processes, logs, environment checks."""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .parsers import SEVERITY_NAMES, elf_info, parse_mi_output, parse_pidin, parse_slog2
from .sdk import ARCHES, runner_binary, sdk_env, sdk_tool
from .target import Target, gdb_for
from .util import ENV_NAME_RE, REMOTE_NAME_RE, Config, QnxMcpError, check_remote_path, quote_argv, run, tail

REMOTE_ROOT = "/tmp/qnx-mcp"
KEEP_CORES = 20
_runs_lock = threading.Lock()


# ---------------------------------------------------------------- run records


def _runs_file(t: Target) -> Path:
    return t.state / "runs.jsonl"


def _save_run(t: Target, rec: dict[str, Any]) -> None:
    t.state.mkdir(parents=True, exist_ok=True)
    with _runs_lock, open(_runs_file(t), "a") as f:
        f.write(json.dumps(rec) + "\n")


def _find_run(t: Target, run_id: str | None) -> dict[str, Any] | None:
    try:
        lines = _runs_file(t).read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        with_rec = json.loads(line)
        if run_id is None and with_rec.get("core_path"):
            return with_rec
        if run_id is not None and with_rec.get("run_id") == run_id:
            return with_rec
    return None


# ---------------------------------------------------------------- deploy_and_run


def _ensure_runner(t: Target, ip: str) -> str:
    local, digest = runner_binary(t.cfg, t.arch)
    remote = f"{REMOTE_ROOT}/bin/qnx-mcp-run-{digest}"
    r = t.ssh(["test", "-x", remote], timeout=15, what="runner check", ip=ip)
    if r.returncode != 0:
        t.ssh(["mkdir", "-p", f"{REMOTE_ROOT}/bin"], timeout=15, what="mkdir", ip=ip)
        t.put(local, remote, timeout=60, ip=ip)
    return remote


def deploy_and_run(
    cfg: Config,
    target: str,
    binary: str,
    args: list[str] | None = None,
    timeout_s: float = 30,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    max_output_kb: int = 64,
    core_wait_s: float = 15,
) -> dict[str, Any]:
    t = Target(cfg, target)
    local = Path(binary).expanduser().resolve()
    if not local.is_file():
        raise QnxMcpError("invalid_argument", f"binary not found on the build host: {local}", hint="Call build first and pass one of its artifacts[].path.")
    info = elf_info(str(local))
    if info is None:
        raise QnxMcpError("invalid_argument", f"{local} is not an ELF file")
    if info["arch"] != t.arch:
        raise QnxMcpError("arch_mismatch", f"{local.name} is {info['arch']} but target {target} is {t.arch}", hint=f"Rebuild with arch={t.arch!r}.")
    if info["qnx"] is False:
        raise QnxMcpError("not_a_qnx_binary", f"{local.name} uses interpreter {info['interpreter']}, not QNX's ldqnx", hint="Build with the QNX toolchain (the build tool uses qcc).")
    if not REMOTE_NAME_RE.match(local.name):
        raise QnxMcpError("invalid_argument", f"binary file name {local.name!r} has unsupported characters")
    if not 0 < timeout_s <= 3600 or not 0 <= core_wait_s <= 300 or not 1 <= max_output_kb <= 4096:
        raise QnxMcpError("invalid_argument", "timeout_s must be in (0, 3600], core_wait_s in [0, 300], max_output_kb in [1, 4096]")
    argv_env = []
    for k, v in (env or {}).items():
        if not ENV_NAME_RE.match(k):
            raise QnxMcpError("invalid_argument", f"bad environment variable name {k!r}")
        argv_env += ["-e", f"{k}={v}"]
    if cwd is not None:
        check_remote_path(cwd, "cwd")

    run_id = "r-" + uuid.uuid4().hex[:10]
    with t.lock(exclusive=True):
        ip = t.require_ip()
        runner = _ensure_runner(t, ip)
        remote_bin = f"{REMOTE_ROOT}/bin/{local.name}"
        t.put(local, remote_bin, timeout=120, ip=ip)
        argv = [runner, "-t", str(int(timeout_s * 1000)), "-m", str(max_output_kb * 1024), "-w", str(int(core_wait_s * 1000)), *argv_env]
        if cwd:
            argv += ["-C", cwd]
        argv += ["--", remote_bin, *[str(a) for a in (args or [])]]
        started = time.time()
        r = t.ssh(argv, timeout=timeout_s + core_wait_s + 30, what="deploy_and_run", ip=ip)
    line = next((l for l in reversed(r.out.splitlines()) if l.startswith("{")), None)
    try:
        res = json.loads(line) if line else None
    except ValueError:
        res = None
    if not res or "usage_error" in res:
        raise QnxMcpError("runner_failed", "the target runner did not return a result", stdout=tail(r.out, 800), stderr=tail(r.err, 800))
    res.pop("runner", None)
    result: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "target": target,
        "binary": str(local),
        "remote_path": remote_bin,
        "args": [str(a) for a in (args or [])],
        "started_at": started,
        "runner_version": res.pop("version", None),
        **res,
    }
    if res.get("exec_error"):
        result["outcome"] = "exec_failed"
    elif res.get("timed_out"):
        result["outcome"] = "timeout"
    elif res.get("signaled"):
        result["outcome"] = "crashed" if res.get("core_expected") else "killed"
    else:
        result["outcome"] = "exited"
    if result.get("core_path"):
        # dumper (as configured by mkqnximage) reuses <name>.core, so keep this run's core under its run_id.
        src = check_remote_path(result["core_path"], "core path")
        dst = f"{REMOTE_ROOT}/cores/{run_id}-{os.path.basename(src)}"
        script = (
            f"mkdir -p {REMOTE_ROOT}/cores && mv -f {quote_argv([src, dst])} && "
            f"cd {REMOTE_ROOT}/cores && ls -t | sed -e '1,{KEEP_CORES}d' | while read f; do rm -f -- \"$f\"; done"
        )
        with t.lock(exclusive=True):
            mv = t.ssh_script(script, timeout=30, what="preserve core")
        if mv.returncode == 0:
            result["dumper_core_path"] = src
            result["core_path"] = dst
        result["next_step"] = f"call get_crash with target={target!r} and run_id={run_id!r} for a source-level backtrace"
    elif res.get("core_expected"):
        result["next_step"] = f"no core reported (dumper={res.get('dumper')}); check target_status.dumper_running"
    _save_run(t, {k: result.get(k) for k in ("run_id", "binary", "remote_path", "pid", "signal_name", "core_path", "started_at")})
    return result


# ---------------------------------------------------------------- get_crash

_SEGV_CODES = {1: "SEGV_MAPERR (address not mapped)", 2: "SEGV_ACCERR (no permission)", 3: "SEGV_STKERR", 4: "SEGV_GPERR (general protection)", 5: "SEGV_IRQERR"}


def _source_context(path: str | None, line: int | None, radius: int = 3) -> list[dict[str, Any]] | None:
    if not path or not line or not os.path.isfile(path):
        return None
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return [{"line": n, "text": lines[n - 1], "current": n == line} for n in range(lo, hi + 1)]


def _frame(f: dict[str, Any], sdk_target: str) -> dict[str, Any]:
    fullname = f.get("fullname")
    lib = f.get("from")
    return {
        "level": int(f.get("level", 0)),
        "function": f.get("func"),
        "file": f.get("file"),
        "fullname": fullname,
        "line": int(f["line"]) if f.get("line") else None,
        "address": f.get("addr"),
        "library": lib,
        "user_code": bool(fullname and os.path.isfile(fullname) and not fullname.startswith(sdk_target)),
    }


def get_crash(
    cfg: Config,
    target: str,
    run_id: str | None = None,
    core_path: str | None = None,
    binary: str | None = None,
    max_frames: int = 32,
    locals_frames: int = 3,
) -> dict[str, Any]:
    t = Target(cfg, target)
    rec = None if core_path and binary else _find_run(t, run_id)
    if run_id and rec is None:
        raise QnxMcpError("invalid_argument", f"unknown run_id {run_id!r} for target {target}")
    remote_core = core_path or (rec or {}).get("core_path")
    local_bin = binary or (rec or {}).get("binary")
    if not remote_core:
        raise QnxMcpError("core_not_found", "no crashed run with a core file is recorded for this target", hint="Run the program with deploy_and_run first, or pass core_path and binary explicitly.")
    if not local_bin or not Path(local_bin).is_file():
        raise QnxMcpError("invalid_argument", f"host binary for the crash not found: {local_bin}", hint="Pass binary= with the unstripped executable that crashed.")
    check_remote_path(remote_core, "core_path")
    env = sdk_env(cfg)
    arch = t.arch
    dest = cfg.state_dir / "cores" / target / (run_id or (rec or {}).get("run_id") or "manual") / os.path.basename(remote_core)
    with t.lock(exclusive=False):
        t.get(remote_core, dest, timeout=300)
    if dest.suffix == ".gz":
        plain = dest.with_suffix("")
        with gzip.open(dest, "rb") as src, open(plain, "wb") as out:
            shutil.copyfileobj(src, out)
        dest = plain

    sysroot = os.path.join(env["QNX_TARGET"], arch)
    libpath = ":".join(os.path.join(sysroot, p) for p in ("lib", "usr/lib", "lib/dll"))
    cmds = [
        "1-thread-info",
        "2-data-evaluate-expression $_siginfo.si_signo",
        "3-data-evaluate-expression $_siginfo.si_code",
        "4-data-evaluate-expression $_siginfo.__data.__fault.__addr",
        '5-interpreter-exec console "x/i $pc"',
        f"6-stack-list-frames 0 {max(0, max_frames - 1)}",
    ]
    for i in range(max(0, locals_frames)):
        cmds += [f"{100 + i}-stack-select-frame {i}", f"{200 + i}-stack-list-variables --simple-values"]
    cmds.append("-gdb-exit")
    argv = [
        sdk_tool(env, gdb_for(arch)), "-q", "-nx", "--interpreter=mi2",
        "-iex", f"set sysroot {sysroot}",
        "-iex", f"set solib-search-path {libpath}",
        "-iex", "set auto-load off",
        "-iex", "set pagination off",
        local_bin, str(dest),
    ]
    r = run(argv, timeout=120, env=env, input=("\n".join(cmds) + "\n").encode())
    if r.timed_out:
        raise QnxMcpError("timeout", "gdb did not finish within 120s")
    mi = parse_mi_output(r.out)
    res = mi["results"]

    def value(tok: str) -> str | None:
        rec_ = res.get(tok, {})
        return rec_.get("value") if rec_.get("class") == "done" else None

    stack = res.get("6", {})
    if stack.get("class") != "done":
        raise QnxMcpError("gdb_failed", "gdb could not read a stack from the core", gdb_error=stack.get("msg"), stderr=tail(r.err, 800), console=tail("".join(mi["console"]), 800))
    frames = [_frame(next(iter(item.values())), env["QNX_TARGET"]) for item in stack.get("stack", [])]
    for f in frames:
        f["source"] = _source_context(f["fullname"], f["line"]) if f["user_code"] else None
    for i in range(max(0, locals_frames)):
        vars_rec = res.get(str(200 + i), {})
        if i < len(frames) and vars_rec.get("class") == "done":
            frames[i]["locals"] = [{"name": v.get("name"), "type": v.get("type"), "value": v.get("value"), "arg": v.get("arg") == "1"} for v in vars_rec.get("variables", [])]

    threads_rec = res.get("1", {})
    threads = [
        {
            "id": th.get("id"),
            "target_id": th.get("target-id"),
            "function": (th.get("frame") or {}).get("func"),
            "file": (th.get("frame") or {}).get("file"),
            "line": int((th.get("frame") or {}).get("line")) if (th.get("frame") or {}).get("line") else None,
        }
        for th in threads_rec.get("threads", [])
    ]
    console = "".join(mi["console"])
    m = re.search(r"Program terminated with signal (\w+), ([^.\n]+)", console)
    signo = value("2")
    code = value("3")
    fault = value("4")
    fault_addr = fault.split()[0] if fault else None
    insn = next((c.strip() for c in mi["console"] if c.lstrip().startswith("=>")), None)
    sig_name = m.group(1) if m else (rec or {}).get("signal_name")
    user = next((f for f in frames if f["user_code"]), None)
    where = f"{user['function']} at {user['file']}:{user['line']}" if user else (f"{frames[0]['function']}" if frames else "unknown location")
    chain = " <- ".join(f"{f['function']}()" for f in frames[:6] if f["function"])
    summary = f"{sig_name or 'signal'}" + (f" ({_SEGV_CODES.get(int(code), code)})" if code and code.isdigit() and sig_name == "SIGSEGV" else "")
    summary += f" at fault address {fault_addr}" if fault_addr else ""
    summary += f" in {where}; stack: {chain}"
    if fault_addr in ("0x0", "0") and sig_name == "SIGSEGV":
        summary += ". Fault address 0x0 means a NULL pointer dereference."
    return {
        "ok": True,
        "target": target,
        "run_id": (rec or {}).get("run_id") or run_id,
        "core_path_target": remote_core,
        "core_path_local": str(dest),
        "binary": local_bin,
        "signal": int(signo) if signo and signo.isdigit() else None,
        "signal_name": sig_name,
        "signal_description": m.group(2) if m else None,
        "si_code": int(code) if code and code.lstrip("-").isdigit() else None,
        "fault_address": fault_addr,
        "faulting_instruction": insn,
        "crashing_frame": user or (frames[0] if frames else None),
        "frames": frames,
        "threads": threads,
        "summary": summary,
    }


# ---------------------------------------------------------------- processes and logs

_PROC_FMT = "%a|%e|%U|%c|%d|%N|%n|%A"
_THREAD_FMT = "%a|%b|%p|%J|%B"
_SPLIT = "@@qnx-mcp-split@@"


def processes(cfg: Config, target: str, name: str | None = None, pid: int | None = None, include_threads: bool = False) -> dict[str, Any]:
    t = Target(cfg, target)
    script = f"{quote_argv(['pidin', '-F', _PROC_FMT])}; echo {_SPLIT}; {quote_argv(['pidin', '-F', _THREAD_FMT])}"
    with t.lock(exclusive=False):
        r = t.ssh_script(script, timeout=30, what="pidin")
    if _SPLIT not in r.out:
        raise QnxMcpError("remote_failed", "pidin output was not recognised", stdout=tail(r.out, 800), stderr=tail(r.err, 800))
    proc_text, thread_text = r.out.split(_SPLIT, 1)
    procs = parse_pidin(proc_text, thread_text.lstrip("\n"))
    total = len(procs)
    if pid is not None:
        procs = [p for p in procs if p["pid"] == pid]
    if name:
        n = name.lower()
        procs = [p for p in procs if n in (p.get("name") or "").lower() or n in (p.get("path") or "").lower()]
    for p in procs:
        states: dict[str, int] = {}
        for th in p.get("threads", []):
            states[th["state"]] = states.get(th["state"], 0) + 1
        p["thread_count"] = len(p.get("threads", []))
        p["thread_states"] = states
        if not include_threads:
            p.pop("threads", None)
    return {"ok": True, "target": target, "total_processes": total, "returned": len(procs), "processes": procs}


def logs(
    cfg: Config,
    target: str,
    buffer: str | None = None,
    process: str | None = None,
    since_s: float | None = None,
    min_severity: str | None = None,
    contains: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    t = Target(cfg, target)
    if min_severity is not None and min_severity not in SEVERITY_NAMES:
        raise QnxMcpError("invalid_argument", f"unknown severity {min_severity!r}", hint=f"Use one of {SEVERITY_NAMES}.")
    if not 1 <= limit <= 5000:
        raise QnxMcpError("invalid_argument", "limit must be 1-5000")
    with t.lock(exclusive=False):
        r = t.ssh_script("date +%s; slog2info -f -s -t", timeout=30, what="slog2info")
    first, _, body = r.out.partition("\n")
    now = int(first) if first.strip().isdigit() else None
    entries = parse_slog2(body)
    total = len(entries)
    if buffer:
        entries = [e for e in entries if e["buffer"] == buffer or e["buffer_set"] == buffer or e["process"] == buffer]
    if process:
        if process.isdigit():
            entries = [e for e in entries if e["pid"] == int(process)]
        else:
            p = process.lower()
            entries = [e for e in entries if p in e["process"].lower()]
    if since_s is not None and now is not None:
        entries = [e for e in entries if e["time"] >= now - since_s]
    if min_severity is not None:
        cut = SEVERITY_NAMES.index(min_severity)
        entries = [e for e in entries if e["severity"] <= cut]
    if contains:
        c = contains.lower()
        entries = [e for e in entries if c in e["message"].lower()]
    matched = len(entries)
    entries = entries[-limit:]
    for e in entries:
        e["time_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(e["time"])) + f".{int(e['time'] * 1000) % 1000:03d}Z"
    return {"ok": True, "target": target, "target_time": now, "total_entries": total, "matched": matched, "returned": len(entries), "truncated": matched > len(entries), "entries": entries}


# ---------------------------------------------------------------- environment check


def environment_check(cfg: Config) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, fix: str | None = None) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, **({"fix": fix} if fix and not ok else {})})

    try:
        env = sdk_env(cfg)
        add("qnx_sdp", True, f"QNX_HOST={env['QNX_HOST']} QNX_TARGET={env['QNX_TARGET']}")
        for tool in ("qcc", "mkqnximage", "ntox86_64-gdb", "ntoaarch64-gdb"):
            p = shutil.which(tool, path=env.get("PATH"))
            add(f"tool:{tool}", bool(p), p or "not found", "Reinstall QNX SDP 8.0 host tools.")
        add("tool:cmake", bool(shutil.which("cmake")), shutil.which("cmake") or "not found (only needed for CMake projects)", "apt install cmake")
    except QnxMcpError as e:
        add("qnx_sdp", False, e.message, e.hint)
    add("kvm", os.access("/dev/kvm", os.W_OK), "/dev/kvm writable" if os.access("/dev/kvm", os.W_OK) else "/dev/kvm not writable: QEMU will be very slow", "sudo adduser $USER kvm, then log in again.")
    for tool in ("qemu-system-x86_64", "ssh", "scp"):
        add(f"tool:{tool}", bool(shutil.which(tool)), shutil.which(tool) or "not found", f"Install {tool} (apt install qemu-system-x86 openssh-client).")
    key = Path(str(cfg.ssh_key) + ".pub")
    add("ssh_key", key.is_file(), str(key), "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519")
    add("dhcp_leases", cfg.leases.is_file(), str(cfg.leases), "Not fatal: IP lookup falls back to mkqnximage --getip (arp).")
    add("targets_dir", True, f"{cfg.targets_dir} (allowlist {list(cfg.allow)})")
    return {"ok": all(c["ok"] for c in checks if c["check"] not in ("tool:cmake", "dhcp_leases", "tool:ntoaarch64-gdb")), "checks": checks}
