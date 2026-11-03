# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Target operations: deploy and run (one-shot and background), file transfer and listing, processes, logs, environment checks."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .parsers import SEVERITY_NAMES, elf_info, parse_pidin, parse_slog2
from .sdk import runner_binary, sdk_env
from .target import Target
from .util import (
    ENV_NAME_RE, REMOTE_NAME_RE, Config, QnxMcpError, check_cancelled, check_host_path, check_remote_path, check_run_id,
    private_dir, quote_argv, tail, uncancellable, within, write_private,
)

REMOTE_ROOT = "/tmp/qnx-mcp"
KEEP_APPS = 20
KEEP_REMOTE_RUNS = 20
KEEP_HOST_RUNS = 40
MAX_BINARY = 256 << 20
INLINE_TEXT = 64 << 10
WRITE_ROOTS = ("/tmp", "/data")
IN_FLIGHT = ("starting", "running")
POLL_MAX_KB = 1024
LIST_MAX = 1000
STOP_ROOTS = ("/tmp", "/data")
SYSTEM_DIRS = ("/proc/boot", "/bin", "/sbin", "/usr", "/system")
STOP_SIGNALS = ("TERM", "INT", "HUP", "QUIT", "USR1", "USR2", "KILL")
_sha_cache: dict[tuple[str, int, int], str] = {}
_sha_lock = threading.Lock()


def _sha256_file(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    with _sha_lock:
        if key in _sha_cache:
            return _sha_cache[key]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with _sha_lock:
        _sha_cache[key] = h.hexdigest()
    return h.hexdigest()


# ---------------------------------------------------------------- run records


def new_run_id() -> str:
    return "r-" + uuid.uuid4().hex[:12]


def runs_dir(t: Target) -> Path:
    return t.state / "runs"


def save_record(t: Target, rec: dict[str, Any]) -> None:
    with t.records_lock():
        write_private(runs_dir(t) / rec["run_id"] / "record.json", json.dumps(rec, indent=1).encode())


def load_record(t: Target, run_id: str) -> dict[str, Any] | None:
    try:
        return json.loads((runs_dir(t) / check_run_id(run_id) / "record.json").read_text())
    except (OSError, ValueError):
        return None


def all_records(t: Target) -> list[dict[str, Any]]:
    recs = []
    if runs_dir(t).is_dir():
        for d in runs_dir(t).iterdir():
            with contextlib.suppress(OSError, ValueError):
                recs.append(json.loads((d / "record.json").read_text()))
    return sorted(recs, key=lambda r: r.get("started_at") or 0)


def _in_flight(t: Target) -> list[dict[str, Any]]:
    """Runs that may still use their directories on the target (any kind, including uncertain starts)."""
    now = time.time()
    return [r for r in all_records(t) if r.get("state") in IN_FLIGHT and (r.get("deadline") or r.get("started_at", 0) + 3600) + 3600 > now]


def _prune_host_runs(t: Target) -> None:
    """Keep the newest KEEP_HOST_RUNS finished records (any kind); never prune a run still in flight."""
    with t.records_lock():
        live = {r["run_id"] for r in _in_flight(t)}
        done = [r for r in all_records(t) if r["run_id"] not in live]
        for r in done[: max(0, len(done) - KEEP_HOST_RUNS)]:
            shutil.rmtree(runs_dir(t) / r["run_id"], ignore_errors=True)


# ---------------------------------------------------------------- uploads


def _parse_runner(text: str, what: str) -> dict[str, Any]:
    """The runner prints exactly one JSON object; parse the whole of it, nothing else."""
    try:
        res = json.loads(text.strip())
    except ValueError:
        res = None
    if not isinstance(res, dict) or res.get("runner") != "qnx-mcp-run":
        raise QnxMcpError("runner_failed", f"the target runner did not return a result for {what}", stdout=tail(text, 800))
    if "usage_error" in res:
        raise QnxMcpError("runner_failed", f"the target runner refused {what}: {res['usage_error']} ({res.get('error')})")
    return res


def _snapshot_binary(local: Path, dest_dir: Path, arch: str) -> tuple[Path, str, dict[str, Any]]:
    """Copy the binary once into the run's own directory and check that copy: later rebuilds of
    the original cannot change what was deployed or what get_crash reads."""
    size = local.stat().st_size
    if size > MAX_BINARY:
        raise QnxMcpError("invalid_argument", f"{local} is {size} bytes; the limit is {MAX_BINARY}")
    data = local.read_bytes()
    snap = dest_dir / local.name
    write_private(snap, data)
    os.chmod(snap, 0o700)
    info = elf_info(str(snap))
    if info is None:
        raise QnxMcpError("invalid_argument", f"{local} is not an ELF file", hint="Pass an executable from build.artifacts[].path.")
    if info["arch"] != arch:
        raise QnxMcpError("arch_mismatch", f"{local.name} is {info['arch']} but the target is {arch}", hint=f"Rebuild with arch={arch!r}.")
    if info["qnx"] is False:
        raise QnxMcpError("not_a_qnx_binary", f"{local.name} uses interpreter {info['interpreter']}, not QNX's ldqnx", hint="Build with the QNX toolchain (the build tool uses qcc).")
    return snap, hashlib.sha256(data).hexdigest(), info


def _upload(t: Target, ip: str, local: Path, remote: str, sha: str, size: int, mode: str, hasher: str | None) -> None:
    """scp to a unique temporary name, verify size, mode and SHA-256 on the target, then rename."""
    tmp = f"{remote}.part-{uuid.uuid4().hex[:8]}"
    t.put(local, tmp, timeout=300, ip=ip)
    try:
        r = t.ssh_script(f"chmod {mode} {quote_argv([tmp])} && {quote_argv([hasher or tmp, '-H', tmp])}", timeout=60, what="upload verification", ip=ip)
        got = _parse_runner(r.out, "upload verification")
        if got.get("sha256") != sha or got.get("size") != size or got.get("mode") != mode:
            raise QnxMcpError(
                "deploy_failed",
                f"uploaded copy of {local.name} does not match (size {got.get('size')} vs {size}, mode {got.get('mode')} vs {mode})",
                hint="Retry; if it repeats, check free space on the target's /data partition.",
                expected_sha256=sha, got_sha256=got.get("sha256"),
            )
        t.ssh(["mv", "-f", tmp, remote], timeout=30, what="upload rename", ip=ip)
    except BaseException:
        with contextlib.suppress(QnxMcpError):
            t.ssh(["rm", "-f", tmp], timeout=15, what="upload cleanup", ip=ip, check=False)
        raise


def prepare(t: Target, ip: str, files: list[tuple[Path, str, str, int, str]], mkdirs: list[str], prune: bool = False) -> str:
    """Make sure the runner and the given files are on the target with the right bytes.

    files: (local, remote, sha256, size, mode). One round trip checks everything; only
    missing or mismatching files are uploaded. With prune, the verified runner removes old run
    and app directories, never one that is locked by a live runner, younger than a few minutes,
    or named by a run this server still has in flight. Returns the runner's remote path.
    """
    runner_local, digest = runner_binary(t.cfg, t.arch)
    runner_sha, runner_size = _sha256_file(runner_local), runner_local.stat().st_size
    runner_remote = f"{REMOTE_ROOT}/bin/qnx-mcp-run-{digest}"
    dirs = sorted({f"{REMOTE_ROOT}/bin", f"{REMOTE_ROOT}/runs", f"{REMOTE_ROOT}/apps", *mkdirs, *(os.path.dirname(f[1]) for f in files)})
    for d in dirs:
        check_remote_path(d)
    parts = [f"mkdir -p {quote_argv(dirs)} || exit 3; R={quote_argv([runner_remote])};"]
    for p in [runner_remote, *(f[1] for f in files)]:
        q = quote_argv([p])
        # exactly one line per file, even if the runner is missing or broken
        parts.append(f"X=; if test -f {q} && test -x \"$R\"; then X=$(\"$R\" -H {q} 2>/dev/null); fi; "
                     f"if test -n \"$X\"; then printf '%s\\n' \"$X\"; else echo none; fi;")
    if prune:
        flight = _in_flight(t)
        keep_runs = [x for r in flight for x in ("-p", r["run_id"])] + [x for d in mkdirs if d.startswith(f"{REMOTE_ROOT}/runs/") for x in ("-p", os.path.basename(d))]
        keep_apps = [x for r in flight if (r.get("remote_path") or "").startswith(f"{REMOTE_ROOT}/apps/") for x in ("-p", r["remote_path"].split("/")[-2])]
        keep_apps += [x for f in files if f[1].startswith(f"{REMOTE_ROOT}/apps/") for x in ("-p", f[1].split("/")[-2])]
        ver = quote_argv([f"qnx-mcp-run {digest}"])
        parts.append(f"if test \"$(\"$R\" -V 2>/dev/null)\" = {ver}; then "
                     f"\"$R\" -G {REMOTE_ROOT}/runs -k {KEEP_REMOTE_RUNS} -a 300 {quote_argv(keep_runs)} >/dev/null 2>&1; "
                     f"\"$R\" -G {REMOTE_ROOT}/apps -k {KEEP_APPS} -a 600 {quote_argv(keep_apps)} >/dev/null 2>&1; fi;")
    r = t.ssh_script(" ".join(parts), timeout=60, what="deploy check", ip=ip)
    lines = [line for line in r.out.split("\n") if line.strip()]
    if len(lines) != len(files) + 1:
        raise QnxMcpError("remote_failed", "deploy check output was not recognised", stdout=tail(r.out, 800))
    states = []
    for line in lines:
        try:
            states.append(json.loads(line))
        except ValueError:
            states.append({})
    if states[0].get("sha256") != runner_sha or states[0].get("mode") != "0755":
        _upload(t, ip, runner_local, runner_remote, runner_sha, runner_size, "0755", None)
    for (local, remote, sha, size, mode), st in zip(files, states[1:]):
        if st.get("sha256") != sha or st.get("mode") != mode:
            _upload(t, ip, local, remote, sha, size, mode, runner_remote)
    return runner_remote


# ---------------------------------------------------------------- one-shot runs


def _run_options(timeout_s: float, core_wait_s: float, max_output_kb: int, env: dict[str, str] | None, cwd: str | None,
                 max_timeout: float, max_kb: int) -> list[str]:
    if not 0 < timeout_s <= max_timeout or not 0 <= core_wait_s <= 300 or not 1 <= max_output_kb <= max_kb:
        raise QnxMcpError("invalid_argument", f"timeout_s must be in (0, {max_timeout:g}], core_wait_s in [0, 300], max_output_kb in [1, {max_kb}]")
    opts = ["-t", str(max(1, int(timeout_s * 1000))), "-m", str(max_output_kb * 1024), "-w", str(int(core_wait_s * 1000))]
    for k, v in (env or {}).items():
        if not ENV_NAME_RE.fullmatch(str(k)) or "\0" in str(v):
            raise QnxMcpError("invalid_argument", f"bad environment variable {k!r}")
        opts += ["-e", f"{k}={v}"]
    if cwd is not None:
        opts += ["-C", check_remote_path(cwd, "cwd")]
    return opts


def _outcome(res: dict[str, Any]) -> str:
    if res.get("exec_error"):
        return "exec_failed"
    if res.get("timed_out"):
        return "timeout"
    if res.get("disconnected") or res.get("runner_stopped_by"):
        return "stopped"
    if res.get("signaled"):
        return "crashed" if res.get("core_expected") else "killed"
    return "exited"


@contextlib.contextmanager
def _discard_unrecorded(t: Target, rid: str) -> Any:
    """A run that fails before its record is written leaves no snapshot directory behind."""
    try:
        yield
    except BaseException:
        if not (runs_dir(t) / rid / "record.json").exists():
            shutil.rmtree(runs_dir(t) / rid, ignore_errors=True)
        raise


def _in_managed_dir(path: str) -> bool:
    return path == REMOTE_ROOT or path.startswith(REMOTE_ROOT + "/")


def _program(cfg: Config, t: Target, binary: str | None, remote_binary: str | None, rid: str) -> dict[str, Any]:
    """What a run starts: a snapshot of a host binary (uploaded content-addressed), or a program already on the target."""
    if (binary is None) == (remote_binary is None):
        raise QnxMcpError("invalid_argument", "pass exactly one of binary (a host path) and remote_binary (a path on the target)",
                          hint="Use binary for something you built, remote_binary for a program installed on the target.")
    if remote_binary is not None:
        remote = check_remote_path(remote_binary, "remote_binary")
        if _in_managed_dir(remote):
            raise QnxMcpError("path_not_allowed", f"remote_binary may not be inside {REMOTE_ROOT}: {remote}",
                              hint="Run your own builds with binary=<host path>; remote_binary is for programs installed on the target.")
        return {"remote_binary": True, "binary": None, "binary_snapshot": None, "binary_sha256": None, "build_id": None, "remote_path": remote}
    local = check_host_path(cfg, binary, "binary")
    if not REMOTE_NAME_RE.fullmatch(local.name):
        raise QnxMcpError("invalid_argument", f"binary file name {local.name!r} has unsupported characters", hint="Use letters, digits, '.', '_', '+' and '-'.")
    try:
        snap, sha, info = _snapshot_binary(local, runs_dir(t) / rid / "bin", t.arch)
    except BaseException:
        shutil.rmtree(runs_dir(t) / rid, ignore_errors=True)
        raise
    return {"remote_binary": False, "binary": str(local), "binary_snapshot": str(snap), "binary_sha256": sha, "build_id": info.get("build_id"),
            "remote_path": f"{REMOTE_ROOT}/apps/{sha[:16]}/{local.name}", "size": info["size"]}


def _runner_refusal(text: str) -> tuple[str, str | None, dict[str, Any]] | None:
    """(usage_error, errno text, whole report) when the runner refused a request, else None."""
    with contextlib.suppress(ValueError):
        res = json.loads(text.strip())
        if isinstance(res, dict) and res.get("runner") == "qnx-mcp-run" and "usage_error" in res:
            return res["usage_error"], res.get("error"), res
    return None


def _install_program(t: Target, ip: str, prog: dict[str, Any], remote_run: str) -> str:
    """Upload the runner (and a host binary); for a target program, check it exists, is executable and is
    outside qnx-mcp's directory (by real path), and record its SHA-256. Returns the runner's path."""
    if not prog["remote_binary"]:
        return prepare(t, ip, [(Path(prog["binary_snapshot"]), prog["remote_path"], prog["binary_sha256"], prog["size"], "0755")], [remote_run], prune=True)
    runner = prepare(t, ip, [], [remote_run], prune=True)
    remote = prog["remote_path"]
    r = t.ssh([runner, "-H", remote, "-x", REMOTE_ROOT], timeout=60, what="remote_binary check", ip=ip, check=False)
    refused = _runner_refusal(r.out)
    if refused:
        msg, err, _ = refused
        if "own directory" in msg:
            raise QnxMcpError("path_not_allowed", f"{remote} resolves into {REMOTE_ROOT}", hint="remote_binary is for programs installed on the target, not qnx-mcp's files.")
        if msg == "open" and err == "No such file or directory":
            raise QnxMcpError("file_not_found", f"{remote} does not exist on {t.name}", hint="Check the path with file_list or processes (path column).")
        raise QnxMcpError("invalid_argument", f"{remote} cannot be run: {msg}" + (f" ({err})" if err else ""), hint="remote_binary must name an executable regular file.")
    info = _parse_runner(r.out, "remote_binary check")
    if not info.get("executable"):
        raise QnxMcpError("invalid_argument", f"{remote} is not executable (mode {info.get('mode')})",
                          hint="Copy it again with file_put and mode='0755', or name the installed executable.")
    prog.update(binary_sha256=info["sha256"], remote_real_path=info["real_path"], remote_size=info["size"])
    return runner


def _program_argv(prog: dict[str, Any]) -> list[str]:
    """Runner options before '--': a target program is checked again just before it starts."""
    return ["-x", REMOTE_ROOT] if prog["remote_binary"] else []


def _record_fields(prog: dict[str, Any]) -> dict[str, Any]:
    keys = ("binary", "binary_snapshot", "binary_sha256", "build_id", "remote_path", "remote_binary", "remote_real_path")
    return {k: prog.get(k) for k in keys}


def _crash_step(target: str, rid: str, rec: dict[str, Any]) -> str:
    if rec.get("remote_binary"):
        return (f"call get_crash with target={target!r}, run_id={rid!r} and binary=<host path of the unstripped build of {rec['remote_path']}>; "
                "the program came from the target, so qnx-mcp holds no copy of it to read symbols from")
    return f"call get_crash with target={target!r} and run_id={rid!r} for a source-level backtrace"


def _finish_record(rec: dict[str, Any], res: dict[str, Any]) -> None:
    rec.update(state="finished", outcome=_outcome(res), finished_at=time.time(),
               **{k: res.get(k) for k in ("pid", "exit_code", "signal", "signal_name", "core_path", "dumper_core_path", "dumper")})


def deploy_and_run(
    cfg: Config,
    target: str,
    binary: str | None = None,
    args: list[str] | None = None,
    timeout_s: float = 30,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    max_output_kb: int = 64,
    core_wait_s: float = 15,
    remote_binary: str | None = None,
) -> dict[str, Any]:
    t = Target(cfg, target)
    args = [str(a) for a in (args or [])]
    opts = _run_options(timeout_s, core_wait_s, max_output_kb, env, cwd, 3600, 4096)
    rid = new_run_id()
    prog = _program(cfg, t, binary, remote_binary, rid)
    with _discard_unrecorded(t, rid), t.lock(exclusive=False):
        ip = t.require_ip()
        remote_run = f"{REMOTE_ROOT}/runs/{rid}"
        runner = _install_program(t, ip, prog, remote_run)
        rec: dict[str, Any] = {
            "run_id": rid, "target": target, "kind": "oneshot", "state": "running", **_record_fields(prog), "remote_run_dir": remote_run,
            "args": args, "env_names": sorted((env or {}).keys()), "runner": runner, "started_at": time.time(),
        }
        save_record(t, rec)
        argv = [runner, "-S", *opts, *_program_argv(prog), "-R", remote_run, "--", prog["remote_path"], *args]
        try:
            r = t.ssh(argv, timeout=timeout_s + core_wait_s + 30, what="deploy_and_run", ip=ip, check=False, hold_stdin=True,
                      max_output=12 * max_output_kb * 1024 + (1 << 20))  # JSON escaping can grow a byte to 6, two streams
            if r.truncated:
                raise QnxMcpError("runner_failed", "the runner's report was larger than its budget and was cut off")
            refused = _runner_refusal(r.out) if prog["remote_binary"] else None
            if refused:
                raise QnxMcpError("path_not_allowed" if "own directory" in refused[0] else "invalid_argument",
                                  f"{prog['remote_path']} was refused just before it started: {refused[0]}", hint="It changed after the check; name an executable outside /tmp/qnx-mcp.")
            res = _parse_runner(r.out, "deploy_and_run")
        except BaseException as e:
            rec.update(state="failed", error=getattr(e, "code", type(e).__name__))
            save_record(t, rec)
            raise
        _finish_record(rec, res)
        save_record(t, rec)
    _prune_host_runs(t)
    for k in ("runner", "version"):
        res.pop(k, None)
    result: dict[str, Any] = {
        "ok": True, "run_id": rid, "target": target, "outcome": _outcome(res), "binary": prog["binary"], "binary_sha256": prog["binary_sha256"],
        "build_id": prog["build_id"], "remote_path": prog["remote_path"], "remote_binary": prog["remote_binary"], "args": args,
        "started_at": rec["started_at"], **res,
    }
    if result.get("core_path"):
        result["next_step"] = _crash_step(target, rid, rec)
    elif res.get("core_expected"):
        result["next_step"] = f"no core reported (dumper={res.get('dumper')}); check target_status.dumper_running"
    if res.get("stdout_truncated") or res.get("stderr_truncated"):
        result["note"] = "output was truncated to max_output_kb per stream; *_bytes give the full sizes"
    return result


# ---------------------------------------------------------------- background runs


def run_start(
    cfg: Config,
    target: str,
    binary: str | None = None,
    args: list[str] | None = None,
    timeout_s: float = 600,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    max_output_kb: int = 1024,
    core_wait_s: float = 15,
    remote_binary: str | None = None,
) -> dict[str, Any]:
    t = Target(cfg, target)
    args = [str(a) for a in (args or [])]
    opts = _run_options(timeout_s, core_wait_s, max_output_kb, env, cwd, 86400, 65536)
    rid = new_run_id()
    prog = _program(cfg, t, binary, remote_binary, rid)
    with _discard_unrecorded(t, rid), t.lock(exclusive=False):
        ip = t.require_ip()
        remote_run = f"{REMOTE_ROOT}/runs/{rid}"
        runner = _install_program(t, ip, prog, remote_run)
        rec: dict[str, Any] = {
            "run_id": rid, "target": target, "kind": "background", "state": "starting", **_record_fields(prog), "remote_run_dir": remote_run,
            "args": args, "env_names": sorted((env or {}).keys()), "runner": runner, "started_at": time.time(),
            "deadline": time.time() + timeout_s,
        }
        save_record(t, rec)  # ownership is on disk before anything is launched
        _reconcile_starts(t, ip, exclude=rid)
        try:
            r = t.ssh([runner, "-B", remote_run, *opts, *_program_argv(prog), "-R", remote_run, "--", prog["remote_path"], *args],
                      timeout=30, what="run_start", ip=ip, check=False)
            refused = _runner_refusal(r.out) if prog["remote_binary"] else None
            if refused:
                raise QnxMcpError("path_not_allowed" if "own directory" in refused[0] else "invalid_argument",
                                  f"{prog['remote_path']} was refused just before it started: {refused[0]}", hint="It changed after the check; name an executable outside /tmp/qnx-mcp.")
            res = _parse_runner(r.out, "run_start")
            if not res.get("started"):
                raise QnxMcpError("runner_failed", f"background run did not start: {res.get('error')}")
        except BaseException as e:
            # Lost acknowledgement, timeout or cancellation: the daemon may be running. Stop it
            # through its control FIFO (bounded, not tied to the cancelled call) and record the outcome.
            stopped = _stop_quietly(t, ip, runner, remote_run)
            rec.update(state="failed", error=getattr(e, "code", type(e).__name__), cleanup=stopped)
            save_record(t, rec)
            raise
        rec.update(state="running", pid=res.get("pid"), runner_pid=res.get("runner_pid"))
        save_record(t, rec)
    return {
        "ok": True, "run_id": rid, "target": target, "state": "running", "pid": res.get("pid"), "runner_pid": res.get("runner_pid"),
        "binary": prog["binary"], "binary_sha256": prog["binary_sha256"], "remote_path": prog["remote_path"], "remote_binary": prog["remote_binary"],
        "args": args, "deadline_s": timeout_s,
        "next_step": f"call run_poll with run_id={rid!r} for output (pass back stdout_next/stderr_next as offsets); run_stop ends it",
    }


def _stop_quietly(t: Target, ip: str, runner: str, remote_run: str) -> str:
    with uncancellable():
        try:
            r = t.ssh([runner, "-K", remote_run, "-w", "3000"], timeout=20, what="stop failed start", ip=ip, check=False)
            res = _parse_runner(r.out, "stop failed start")
            return "stopped" if res.get("acknowledged") else ("not running" if not res.get("was_running") else "stop not acknowledged")
        except QnxMcpError as e:
            return f"stop failed: {e.code}"


def _reconcile_starts(t: Target, ip: str, exclude: str) -> None:
    """A start whose outcome was never recorded (server killed mid-call) is stopped and marked failed."""
    for rec in all_records(t):
        if rec.get("state") == "starting" and rec["run_id"] != exclude and time.time() - rec.get("started_at", 0) > 60:
            rec.update(state="failed", error="start not acknowledged", cleanup=_stop_quietly(t, ip, rec["runner"], rec["remote_run_dir"]))
            save_record(t, rec)


def _owned_background(t: Target, run_id: str) -> dict[str, Any]:
    rec = load_record(t, run_id)
    if rec is None or rec.get("kind") != "background":
        raise QnxMcpError("invalid_argument", f"no background run {run_id!r} was started by this server on {t.name}",
                          hint="Use a run_id returned by run_start; runs of other servers or targets are not accessible.")
    return rec


def _poll_once(t: Target, ip: str, rec: dict[str, Any], out_off: int, err_off: int, max_bytes: int) -> dict[str, Any]:
    argv = [rec["runner"], "-P", rec["remote_run_dir"], "-O", str(out_off), "-E", str(err_off), "-L", str(max_bytes)]
    budget = 12 * max_bytes + (256 << 10)  # each byte can become \u00XX in JSON, two streams, plus the status
    r = t.ssh(argv, timeout=30, what="run_poll", ip=ip, check=False, max_output=budget)
    if r.returncode == 127:  # runner missing (for example the target's /tmp was cleaned): reinstall it
        rec["runner"] = prepare(t, ip, [], [])
        argv[0] = rec["runner"]
        r = t.ssh(argv, timeout=30, what="run_poll", ip=ip, check=False, max_output=budget)
    if r.truncated:
        raise QnxMcpError("runner_failed", "run_poll output exceeded its budget and was cut off", hint="Poll again with a smaller max_kb.")
    return _parse_runner(r.out, "run_poll")


def _apply_poll(t: Target, rec: dict[str, Any], p: dict[str, Any]) -> str:
    status = p.get("status")
    if isinstance(status, dict):
        if rec.get("state") != "finished":
            _finish_record(rec, status)
            save_record(t, rec)
        return "finished"
    if p.get("alive"):
        return "running"
    if rec.get("state") == "running":
        rec.update(state="lost", finished_at=time.time())
        save_record(t, rec)
    return "lost"


def run_poll(cfg: Config, target: str, run_id: str, stdout_offset: int = 0, stderr_offset: int = 0, max_kb: int = 64, wait_s: float = 0) -> dict[str, Any]:
    t = Target(cfg, target)
    check_run_id(run_id)
    if stdout_offset < 0 or stderr_offset < 0 or not 1 <= max_kb <= POLL_MAX_KB or not 0 <= wait_s <= 60:
        raise QnxMcpError("invalid_argument", f"offsets must be >= 0, max_kb in [1, {POLL_MAX_KB}], wait_s in [0, 60]")
    with t.lock(exclusive=False):
        rec = _owned_background(t, run_id)  # read inside the target lock
        ip = t.require_ip()
        deadline = time.monotonic() + wait_s
        while True:
            p = _poll_once(t, ip, rec, stdout_offset, stderr_offset, max_kb * 1024)
            state = _apply_poll(t, rec, p)
            new = any((p.get(s) or {}).get("next", 0) > off for s, off in (("stdout", stdout_offset), ("stderr", stderr_offset)))
            if state != "running" or new or time.monotonic() >= deadline:
                break
            check_cancelled()
            time.sleep(0.5)
    out, err = p.get("stdout") or {}, p.get("stderr") or {}
    result: dict[str, Any] = {
        "ok": True, "run_id": run_id, "target": target, "state": state, "alive": bool(p.get("alive")), "pid": rec.get("pid"),
        "stdout": out.get("data", ""), "stdout_offset": out.get("offset", stdout_offset), "stdout_next": out.get("next", stdout_offset),
        "stdout_size": out.get("size"), "stderr": err.get("data", ""), "stderr_offset": err.get("offset", stderr_offset),
        "stderr_next": err.get("next", stderr_offset), "stderr_size": err.get("size"),
    }
    status = p.get("status")
    if isinstance(status, dict):
        result["outcome"] = _outcome(status)
        result["result"] = {k: v for k, v in status.items() if k not in ("runner", "version")}
        if status.get("core_path"):
            result["next_step"] = _crash_step(target, run_id, rec)
    elif state == "lost":
        result["note"] = "the runner is gone without a final status (target rebooted or the run directory was removed)"
    more = out.get("next", 0) < (out.get("size") or 0) or err.get("next", 0) < (err.get("size") or 0)
    result["more_output"] = more
    return result


def run_stop(cfg: Config, target: str, run_id: str, grace_s: float = 5) -> dict[str, Any]:
    t = Target(cfg, target)
    check_run_id(run_id)
    if not 0 <= grace_s <= 60:
        raise QnxMcpError("invalid_argument", "grace_s must be in [0, 60]")
    with t.lock(exclusive=False):
        rec = _owned_background(t, run_id)  # read inside the target lock
        ip = t.require_ip()
        # The request goes through the run's control FIFO, read only by the runner that owns the run.
        r = t.ssh([rec["runner"], "-K", rec["remote_run_dir"], "-w", str(int(grace_s * 1000))], timeout=grace_s + 30, what="run_stop", ip=ip, check=False)
        stop = _parse_runner(r.out, "run_stop")
        p = _poll_once(t, ip, rec, 0, 0, 1)
        state = _apply_poll(t, rec, p)
    status = p.get("status") if isinstance(p.get("status"), dict) else {}
    result: dict[str, Any] = {
        "ok": True, "run_id": run_id, "target": target, "was_running": bool(stop.get("was_running")),
        "acknowledged": bool(stop.get("acknowledged")), "state": state, "outcome": _outcome(status) if status else None,
        "result": {k: v for k, v in status.items() if k not in ("runner", "version")} or None,
    }
    if stop.get("was_running") and not stop.get("acknowledged"):
        result["note"] = "the runner did not finish within grace_s; it still enforces the run's deadline. Poll again or call run_stop with a longer grace_s."
    return result


# ---------------------------------------------------------------- file transfer


def _remote_hash(t: Target, ip: str, runner: str, remote: str) -> dict[str, Any] | None:
    r = t.ssh([runner, "-H", remote], timeout=120, what="remote hash", ip=ip, check=False)
    with contextlib.suppress(QnxMcpError):
        return _parse_runner(r.out, "remote hash")
    return None


def _write_root(remote: str) -> tuple[str, str]:
    """Split a file_put destination into (root, path below root); refuse qnx-mcp's own directory."""
    for root in WRITE_ROOTS:
        if remote.startswith(root + "/") and len(remote) > len(root) + 1:
            if remote == REMOTE_ROOT or remote.startswith(REMOTE_ROOT + "/"):
                break
            return root, remote[len(root) + 1:]
    raise QnxMcpError("path_not_allowed", f"file_put only writes files below {' or '.join(WRITE_ROOTS)} and never into {REMOTE_ROOT}: {remote}",
                      hint="Choose a destination such as /tmp/myapp/config.txt or /data/home/...")


def _dest(t: Target, ip: str, runner: str, root: str, rel: str, extra: list[str]) -> dict[str, Any]:
    r = t.ssh([runner, "-D", rel, "-r", root, "-x", REMOTE_ROOT, *extra], timeout=30, what="destination check", ip=ip, check=False)
    try:
        return _parse_runner(r.out, "destination check")
    except QnxMcpError as e:
        msg = e.message.split("refused destination check: ")[-1]
        code = "file_exists" if "destination exists" in msg else "path_not_allowed"
        raise QnxMcpError(code, f"{root}/{rel}: {msg}", hint="Pass overwrite=true to replace an existing file." if code == "file_exists"
                          else "Use a plain file path whose parent directories are not symlinks.") from None


def _staging(cfg: Config) -> Path:
    return private_dir(cfg.state_dir / "staging")


def file_put(cfg: Config, target: str, local_path: str, remote_path: str, mode: str = "0644", overwrite: bool = False, max_mb: int = 64) -> dict[str, Any]:
    t = Target(cfg, target)
    local = check_host_path(cfg, local_path, "local_path")
    remote = check_remote_path(remote_path, "remote_path")
    root, rel = _write_root(remote)
    if not re.fullmatch(r"0[0-7]{3}", mode):
        raise QnxMcpError("invalid_argument", f"mode must be octal like 0644, got {mode!r}")
    if not 1 <= max_mb <= 1024:
        raise QnxMcpError("invalid_argument", "max_mb must be 1-1024")
    cap = max_mb << 20
    # Snapshot first, with the cap enforced while copying: the source cannot grow or change afterwards.
    snap = _staging(cfg) / f"put-{uuid.uuid4().hex[:12]}"
    h = hashlib.sha256()
    size = 0
    try:
        with open(local, "rb") as src, open(snap, "xb") as dst:
            os.chmod(snap, 0o600)
            for chunk in iter(lambda: src.read(1 << 20), b""):
                size += len(chunk)
                if size > cap:
                    raise QnxMcpError("file_too_large", f"{local} is larger than max_mb={max_mb}")
                h.update(chunk)
                dst.write(chunk)
        sha = h.hexdigest()
        with t.lock(exclusive=False):
            ip = t.require_ip()
            runner = prepare(t, ip, [], [])
            existing = _dest(t, ip, runner, root, rel, ["-c"]).get("exists")
            if existing and not overwrite:
                raise QnxMcpError("file_exists", f"{remote} already exists on {target}", hint="Pass overwrite=true to replace it.")
            tmp_name = f".{os.path.basename(remote)}.part-{uuid.uuid4().hex[:8]}"
            tmp = f"{os.path.dirname(remote)}/{tmp_name}"
            t.put(snap, tmp, timeout=300, ip=ip)
            try:
                r = t.ssh_script(f"chmod {mode} {quote_argv([tmp])} && {quote_argv([runner, '-H', tmp])}", timeout=60, what="upload verification", ip=ip)
                got = _parse_runner(r.out, "upload verification")
                if got.get("sha256") != sha or got.get("size") != size or got.get("mode") != mode:
                    raise QnxMcpError("deploy_failed", f"uploaded copy of {local.name} does not match", expected_sha256=sha, got_sha256=got.get("sha256"))
                _dest(t, ip, runner, root, rel, ["-I", tmp_name] + ([] if overwrite else ["-n"]))
            except BaseException:
                with uncancellable(), contextlib.suppress(QnxMcpError):
                    t.ssh(["rm", "-f", tmp], timeout=15, what="upload cleanup", ip=ip, check=False)
                raise
    finally:
        with contextlib.suppress(OSError):
            snap.unlink()
    return {"ok": True, "target": target, "local_path": str(local), "remote_path": remote, "size": size, "sha256": sha, "mode": mode,
            "replaced": bool(existing)}


def _install(src: Path, parent: Path, name: str, overwrite: bool) -> None:
    """Copy src into a directory through a descriptor of that directory: a unique temporary name,
    then link (never replaces) or, with overwrite, rename over a regular file. The directory itself
    is not modified otherwise (no chmod), and a symlink or directory at the destination is refused."""
    dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    tmp = f".{name}.{uuid.uuid4().hex[:8]}.part"
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None and (not overwrite or not stat_isreg(st.st_mode)):
            raise QnxMcpError("file_exists", f"{parent / name} already exists" + ("" if stat_isreg(st.st_mode) else " and is not a regular file"),
                              hint="Pass overwrite=true to replace a regular file, or choose another local_path.")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 20)
            if overwrite:
                os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            else:
                try:
                    os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
                except FileExistsError:
                    raise QnxMcpError("file_exists", f"{parent / name} appeared while downloading", hint="Pass overwrite=true, or choose another local_path.") from None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
    finally:
        os.close(dfd)


def stat_isreg(mode: int) -> bool:
    return stat.S_ISREG(mode)


def file_get(cfg: Config, target: str, remote_path: str, local_path: str | None = None, overwrite: bool = False, max_mb: int = 16) -> dict[str, Any]:
    t = Target(cfg, target)
    remote = check_remote_path(remote_path, "remote_path")
    if not 1 <= max_mb <= 1024:
        raise QnxMcpError("invalid_argument", "max_mb must be 1-1024")
    parent = name = None
    if local_path:
        dest = Path(local_path).expanduser()
        if not dest.is_absolute() or not dest.name or dest.name in (".", ".."):
            raise QnxMcpError("invalid_argument", "local_path must be an absolute file path")
        parent, name = Path(os.path.realpath(dest.parent)), dest.name
        if cfg.workspace and not within(parent, cfg.workspace):
            raise QnxMcpError("path_not_allowed", f"{dest} is outside the workspace", hint="Use a path inside QNX_MCP_WORKSPACE or omit local_path.")
        if not parent.is_dir():
            raise QnxMcpError("invalid_argument", f"directory {parent} does not exist on the build host")
    staged = _staging(cfg) / f"get-{uuid.uuid4().hex[:12]}"
    try:
        with t.lock(exclusive=False):
            ip = t.require_ip()
            runner = prepare(t, ip, [], [])
            info = _remote_hash(t, ip, runner, remote)
            if info is None:
                raise QnxMcpError("file_not_found", f"{remote} is not a readable regular file on {target}")
            if info["size"] > max_mb << 20:
                raise QnxMcpError("file_too_large", f"{remote} is {info['size']} bytes, above max_mb={max_mb}", hint="Raise max_mb (up to 1024).")
            # Exactly the bytes that were hashed are copied; a file that changed in between fails the check.
            t.fetch(runner, remote, staged, info["size"], timeout=300, ip=ip)
            if staged.stat().st_size != info["size"] or _sha256_file(staged) != info["sha256"]:
                raise QnxMcpError("copy_failed", f"downloaded copy of {remote} does not match the target's file (it changed during the copy)")
        if parent is None:
            final = private_dir(cfg.state_dir / "files" / target / uuid.uuid4().hex[:12]) / os.path.basename(remote)
            os.replace(staged, final)
        else:
            _install(staged, parent, name, overwrite)
            final = parent / name
    finally:
        with contextlib.suppress(OSError):
            staged.unlink()
    result: dict[str, Any] = {"ok": True, "target": target, "remote_path": remote, "local_path": str(final), "size": info["size"],
                              "sha256": info["sha256"], "mode": info.get("mode")}
    if info["size"] <= INLINE_TEXT:
        with contextlib.suppress(UnicodeDecodeError, OSError):
            result["text"] = final.read_bytes().decode("utf-8")
    return result


def file_list(cfg: Config, target: str, path: str, max_entries: int = 200) -> dict[str, Any]:
    t = Target(cfg, target)
    remote = check_remote_path(path, "path") if path != "/" else path
    if not 1 <= max_entries <= LIST_MAX:
        raise QnxMcpError("invalid_argument", f"max_entries must be 1-{LIST_MAX}")
    with t.lock(exclusive=False):
        ip = t.require_ip()
        runner = prepare(t, ip, [], [])
        # a name can be 6 times longer in JSON (control bytes), a link target too
        r = t.ssh([runner, "-l", remote, "-L", str(max_entries)], timeout=60, what="file_list", ip=ip, check=False,
                  max_output=max_entries * (12 << 10) + (64 << 10))
    refused = _runner_refusal(r.out)
    if refused:
        msg, err, _ = refused
        if msg == "no such directory":
            raise QnxMcpError("file_not_found", f"{remote} does not exist on {target}", hint="List its parent directory to see what is there.")
        if msg == "not a directory":
            raise QnxMcpError("invalid_argument", f"{remote} is not a directory", hint="Use file_get to read a file; file_list lists directories.")
        raise QnxMcpError("remote_failed", f"cannot list {remote}: {msg} ({err})")
    if r.truncated:
        raise QnxMcpError("runner_failed", "the listing was larger than its budget and was cut off", hint="Lower max_entries.")
    res = _parse_runner(r.out, "file_list")
    for e in res.get("entries") or []:
        if isinstance(e.get("mtime"), int):
            e["mtime_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e["mtime"]))
    return list_result(target, remote, res)


def list_result(target: str, remote: str, res: dict[str, Any]) -> dict[str, Any]:
    entries = res.get("entries") or []
    total = int(res.get("total") or 0)
    complete = res.get("complete") is True
    out: dict[str, Any] = {"ok": True, "target": target, "path": remote, "real_path": res.get("real_path"),
                           "real_path_verified": res.get("real_path_verified") is True, "complete": complete,
                           "total_entries": total, "returned": len(entries), "truncated": total > len(entries) or not complete, "entries": entries}
    notes = []
    if not complete:
        notes.append(f"reading the directory failed after {total} entries ({res.get('read_error')}); the listing and total_entries are incomplete")
    elif total > len(entries):
        notes.append(f"only the first {len(entries)} of {total} entries by name are listed; raise max_entries (up to {LIST_MAX}) or list a subdirectory")
    if not out["real_path_verified"]:
        notes.append("real_path was resolved separately and does not name the directory that was read (it changed during the call)")
    if notes:
        out["note"] = "; ".join(notes)
    return out


def stop_refusal(pid: int, name: str | None, path: str | None) -> str | None:
    """Why process_stop must leave this process alone, judged from what pidin reports (None: allowed so far;
    the runner checks again on the target through the process itself, including the real path)."""
    if pid <= 1:
        return "pid 1 is procnto, the QNX microkernel and process manager"
    if (name or "").startswith("procnto") or "procnto" in os.path.basename(path or ""):
        return "it is procnto, the QNX microkernel and process manager"
    if not path:
        return "pidin reports no executable path for it"
    exe = path if path.startswith("/") else "/" + path  # pidin shows most paths without the leading '/'
    if any(c in ("", ".", "..") for c in exe.split("/")[1:]):
        return f"its executable path {path!r} is not a plain absolute path"
    for d in SYSTEM_DIRS:
        if exe == d or exe.startswith(d + "/"):
            return f"its executable {exe} is in {d}, a system directory"
    if exe.startswith(f"{REMOTE_ROOT}/bin/"):
        return "it is qnx-mcp's own runner, which enforces the deadline of a run; use run_stop for runs"
    if not any(exe.startswith(r + "/") for r in STOP_ROOTS):
        return f"its executable {exe} is not below {' or '.join(STOP_ROOTS)}"
    return None


def process_stop(cfg: Config, target: str, pid: int, signal: str = "TERM", grace_s: float = 2, kill_after_grace: bool = False) -> dict[str, Any]:
    t = Target(cfg, target)
    if signal not in STOP_SIGNALS:
        raise QnxMcpError("invalid_argument", f"signal must be one of {', '.join(STOP_SIGNALS)}")
    if not 0 <= grace_s <= 60:
        raise QnxMcpError("invalid_argument", "grace_s must be in [0, 60]")
    hint = "process_stop only stops programs whose executable is below /tmp or /data (yours or ones installed there), never system processes."
    if pid <= 1:
        raise QnxMcpError("process_not_allowed", f"process {pid} is not stopped: {stop_refusal(pid, None, None)}", hint=hint)
    with t.lock(exclusive=False):
        ip = t.require_ip()
        runner = prepare(t, ip, [], [])
        # Discovery: the process's creation token (start time) from its /proc entry, and its path from pidin.
        r = t.ssh_script(f"{quote_argv([runner, '-Y', str(pid)])}; echo {_SPLIT}; {quote_argv(['pidin', '-p', str(pid), '-F', _PROC_FMT])}",
                         timeout=30, what="process discovery", ip=ip, check=False)
        ident_text, _, pidin_text = r.out.partition(_SPLIT)
        refused = _runner_refusal(ident_text)
        found = [p for p in parse_pidin(pidin_text.lstrip("\n"), "") if p["pid"] == pid]
        if (refused and refused[0] == "no such process") or not found:
            raise QnxMcpError("process_not_found", f"no process {pid} on {target}", hint="Call processes to find the pid (it may have exited).")
        ident = _parse_runner(ident_text, "process discovery")
        proc = found[0]
        about = {k: proc.get(k) for k in ("pid", "ppid", "name", "path", "args")}
        if ident.get("path") != proc.get("path"):
            raise QnxMcpError("process_not_found", f"process {pid} changed while it was inspected", hint="Call processes again and retry with the current pid.", process=about)
        why = "it is a critical process" if ident.get("critical") else stop_refusal(pid, proc.get("name"), proc.get("path"))
        if why:
            raise QnxMcpError("process_not_allowed", f"process {pid} ({proc.get('name')}) is not stopped: {why}", hint=hint, process=about)
        argv = [runner, "-Z", str(pid), "-T", str(ident["start_ns"]), "-q", proc["path"], "-s", "SIG" + signal, "-w", str(int(grace_s * 1000)),
                *(["-f"] if kill_after_grace else []), *[x for root in STOP_ROOTS for x in ("-A", root)],
                *[x for d in (*SYSTEM_DIRS, f"{REMOTE_ROOT}/bin") for x in ("-F", d)]]
        r = t.ssh(argv, timeout=grace_s + 30, what="process_stop", ip=ip, check=False)
    return stop_result(target, pid, proc, r.out, grace_s, hint)


def stop_result(target: str, pid: int, proc: dict[str, Any], text: str, grace_s: float, hint: str) -> dict[str, Any]:
    """Turn the runner's report into the tool result. A state that could not be read is an error, never an exit."""
    about = {k: proc.get(k) for k in ("pid", "ppid", "name", "path", "args")}
    refused = _runner_refusal(text)
    if refused:
        msg, err, rep_ = refused
        if msg == "no such process":
            raise QnxMcpError("process_not_found", f"process {pid} exited before it could be stopped", hint="Call processes to check.")
        if msg.startswith("changed: "):
            code = "process_not_found" if "start time differs" in msg else "process_not_allowed"
            raise QnxMcpError(code, f"process {pid} changed after it was checked: {msg[9:]}; no signal was sent",
                              hint="Call processes again; if it is still the program you mean, call process_stop again.", process=about)
        if msg.startswith("refused: "):
            where = f" ({rep_['executable']} resolves to {rep_['real_path']})" if rep_.get("real_path") else ""
            raise QnxMcpError("process_not_allowed", f"process {pid} ({proc.get('name')}) is not stopped: {msg[9:]}{where}" + (f" ({err})" if err else ""),
                              hint=hint, process=about)
        raise QnxMcpError("remote_failed", f"process_stop of {pid} failed: {msg}" + (f" ({err})" if err else ""))
    res = _parse_runner(text, "process_stop")
    sent = {k: res.get(k) for k in ("signal", "signal_sent", "sigkill_sent")}
    if res.get("state_error"):
        raise QnxMcpError("remote_failed", f"{res.get('signal')} was sent to process {pid}, but its state could not be read afterwards ({res['state_error']})",
                          hint=f"Call processes(pid={pid}) to see whether it is still running.", process=about, **sent)
    if res.get("sigkill_withheld"):
        raise QnxMcpError("process_not_allowed", f"{res.get('signal')} was sent to process {pid} and it did not exit; SIGKILL was not sent because "
                          f"{res['sigkill_withheld']}", hint=f"Call processes(pid={pid}) to see what it runs now.", process=about, **sent)
    result: dict[str, Any] = {
        "ok": True, "target": target, "pid": pid, "name": proc.get("name"), "args": proc.get("args"), "ppid": res.get("ppid"), "uid": res.get("uid"),
        **{k: res.get(k) for k in ("start_ns", "executable", "real_path", "signal", "signal_sent", "already_exiting", "sigkill_sent", "exited",
                                   "waited_ms", "warnings")},
    }
    result["still_running"] = res.get("exited") is False
    if result["still_running"]:
        result["next_step"] = (f"process {pid} is still running after {grace_s:g} s; call process_stop again with kill_after_grace=true "
                               "(or signal='KILL'), or check its threads with processes(include_threads=true)")
    return result


# ---------------------------------------------------------------- processes and logs

_PROC_FMT = "%a|%e|%U|%c|%d|%N|%n|%A"
_THREAD_FMT = "%a|%b|%p|%J|%B"
_SPLIT = "@@qnx-mcp-split@@"


def processes(cfg: Config, target: str, name: str | None = None, pid: int | None = None, include_threads: bool = False) -> dict[str, Any]:
    t = Target(cfg, target)
    script = f"{quote_argv(['pidin', '-F', _PROC_FMT])} && echo {_SPLIT} && {quote_argv(['pidin', '-F', _THREAD_FMT])}"
    with t.lock(exclusive=False):
        r = t.ssh_script(script, timeout=30, what="pidin")
    if _SPLIT not in r.out or r.truncated:
        raise QnxMcpError("remote_failed", "pidin output was not recognised or too large", stdout=tail(r.out, 800), stderr=tail(r.err, 800))
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
        r = t.ssh_script("date +%s && slog2info -f -s -t", timeout=30, what="slog2info")
    first, _, body = r.out.partition("\n")
    if not first.strip().isdigit():
        raise QnxMcpError("remote_failed", "slog2info output was not recognised", stdout=tail(r.out, 400))
    now = int(first)
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
    if since_s is not None:
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
    return {"ok": True, "target": target, "target_time": now, "total_entries": total, "matched": matched, "returned": len(entries),
            "truncated": matched > len(entries), "source_truncated": r.truncated, "entries": entries}


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
    add("allowlist", bool(cfg.allow), f"QNX_MCP_ALLOW_TARGETS={','.join(cfg.allow)}" if cfg.allow else "QNX_MCP_ALLOW_TARGETS is not set: every target is refused",
        "Set QNX_MCP_ALLOW_TARGETS (e.g. 'dev*', or '*' for every target in the targets dir).")
    add("targets_dir", True, str(cfg.targets_dir))
    add("workspace", True, ", ".join(str(w) for w in cfg.workspace) if cfg.workspace else "not set: host paths are not confined (set QNX_MCP_WORKSPACE to confine them)")
    for c in checks:
        if c["check"] in ("tool:cmake", "dhcp_leases", "tool:ntoaarch64-gdb"):
            c["optional"] = True
    return {"ok": all(c["ok"] for c in checks if not c.get("optional")), "checks": checks}
