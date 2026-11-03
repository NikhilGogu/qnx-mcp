# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""qnx-mcp: MCP server (stdio) with structured tools for QNX OS 8.0 development."""

from __future__ import annotations

import json
import logging
import sys
import traceback
from typing import Annotated, Any, Callable, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__, ops, sdk
from .target import MKQNXIMAGE_OPTIONS, Target, list_targets
from .util import Config, QnxMcpError

log = logging.getLogger("qnx_mcp")

INSTRUCTIONS = """\
qnx-mcp gives you structured, bounded access to QNX OS 8.0 targets (QEMU virtual machines
built with mkqnximage, reachable over SSH) and to the QNX SDP 8.0 toolchain on the build host.

Typical loop:
  1. target_list -> target_start (or target_create for a new VM) -> target_status.
  2. build (qcc/make/cmake) -> read diagnostics[] (file, line, column, severity, message), fix, rebuild.
  3. deploy_and_run with args and a timeout -> outcome is exited | crashed | killed | timeout | exec_failed.
  4. outcome == crashed -> get_crash(run_id) -> source-level backtrace with locals and source lines.
  5. processes (pidin as JSON) and logs (slog2info, filtered) to inspect the running system.
Every tool returns JSON with ok=true, or ok=false plus error {code, message, hint}; follow the hint.
Paths in build/deploy/get_crash arguments are paths on the build host, not on the target.
Never assume a target is yours unless target_list shows it; the server refuses names outside its allowlist.
"""

mcp = MCPServer("qnx-mcp", instructions=INSTRUCTIONS, version=__version__)
CFG = Config.from_env()

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
MUT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


# ---------------------------------------------------------------- output schemas


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorInfo(_M):
    code: str = Field(description="Stable code: invalid_argument, target_not_found, target_not_allowed, target_exists, target_down, target_busy, ssh_failed, host_key_mismatch, timeout, sdk_missing, tool_missing, build_failed, arch_mismatch, not_a_qnx_binary, deploy_failed, runner_failed, core_not_found, gdb_failed, mkqnximage_failed, internal_error")
    message: str
    hint: str | None = Field(None, description="What to do next")
    details: dict[str, Any] | None = None


class TargetInfo(_M):
    ok: bool = True
    name: str
    state: Literal["running", "stopped", "booting"]
    qemu_pid: int | None = None
    arch: str
    ram: str | None = None
    cpus: int | None = None
    mac: str | None = None
    ip: str | None = None
    directory: str
    ssh: Any = Field(None, description="'ok' or an error object from the SSH probe")
    uname: str | None = None
    target_time: int | None = Field(None, description="Target clock, Unix seconds")
    dumper_running: bool | None = Field(None, description="Whether /proc/dumper exists (cores will be written)")
    was_running: bool | None = None


class TargetList(_M):
    ok: bool
    targets_dir: str
    allowlist: list[str]
    targets: list[TargetInfo]


class Diagnostic(_M):
    file: str | None
    line: int | None
    column: int | None
    severity: Literal["error", "warning", "note"]
    message: str
    option: str | None = Field(None, description="Warning option such as -Wunused-variable")
    source: str = Field(description="gcc-json, gcc-text, linker or toolchain")
    function: str | None = None
    notes: list[dict[str, Any]] | None = None
    fixits: list[dict[str, Any]] | None = None


class Artifact(_M):
    path: str
    arch: str
    type: str
    interpreter: str | None
    qnx: bool | None = Field(description="True if linked against QNX's ldqnx loader (None for static)")
    debug_info: bool
    size: int


class BuildStep(_M):
    argv: list[str]
    exit_code: int | None
    timed_out: bool
    duration_s: float
    output: str


class BuildResult(_M):
    ok: bool
    system: Literal["qcc", "make", "cmake"]
    arch: str
    directory: str
    duration_s: float
    error_count: int
    warning_count: int
    diagnostics: list[Diagnostic]
    artifacts: list[Artifact]
    steps: list[BuildStep]
    error: ErrorInfo | None = None
    warning: str | None = None


class RunResult(_M):
    ok: bool
    run_id: str = Field(description="Pass to get_crash")
    target: str
    outcome: Literal["exited", "crashed", "killed", "timeout", "exec_failed"]
    binary: str
    remote_path: str
    args: list[str]
    pid: int
    exit_code: int | None
    signal: int | None
    signal_name: str | None
    exec_error: str | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stdout_bytes: int
    stdout_truncated: bool
    stderr: str
    stderr_bytes: int
    stderr_truncated: bool
    dumper: str = Field(description="registered/notified/timeout/unavailable/not_applicable: state of the dumper notification")
    core_path: str | None = Field(description="Exact core path reported by dumper (DCMD_DUMPER_GETPATH)")
    next_step: str | None = None


class SourceLine(_M):
    line: int
    text: str
    current: bool


class Frame(_M):
    level: int
    function: str | None
    file: str | None
    fullname: str | None
    line: int | None
    address: str | None
    library: str | None
    user_code: bool = Field(description="True when the frame's source file exists on the build host outside the SDP")
    source: list[SourceLine] | None = None
    locals: list[dict[str, Any]] | None = None


class CrashReport(_M):
    ok: bool
    target: str
    run_id: str | None
    summary: str
    signal: int | None
    signal_name: str | None
    signal_description: str | None
    si_code: int | None
    fault_address: str | None
    faulting_instruction: str | None
    crashing_frame: Frame | None
    frames: list[Frame]
    threads: list[dict[str, Any]]
    core_path_target: str
    core_path_local: str
    binary: str


class ProcessInfo(_M):
    pid: int
    ppid: int | None = None
    uid: int | None = None
    name: str | None = None
    path: str | None = None
    args: str | None = None
    code_bytes: int | None = None
    data_bytes: int | None = None
    thread_count: int
    thread_states: dict[str, int]
    threads: list[dict[str, Any]] | None = None


class ProcessList(_M):
    ok: bool
    target: str
    total_processes: int
    returned: int
    processes: list[ProcessInfo]


class LogEntry(_M):
    time: float = Field(description="Unix seconds (target clock)")
    time_iso: str
    process: str
    pid: int | None
    buffer_set: str
    buffer: str
    thread: int
    code: int
    severity: int = Field(description="0 shutdown ... 7 debug2")
    severity_name: str
    message: str


class LogResult(_M):
    ok: bool
    target: str
    target_time: int | None
    total_entries: int
    matched: int
    returned: int
    truncated: bool
    entries: list[LogEntry]


class EnvCheck(_M):
    ok: bool
    checks: list[dict[str, Any]]


# ---------------------------------------------------------------- plumbing


def _result(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, default=str, separators=(",", ":")))],
        structured_content=data,
        is_error=data.get("ok") is False,
    )


def _call(fn: Callable[[], dict[str, Any]]) -> CallToolResult:
    try:
        return _result(fn())
    except QnxMcpError as e:
        return _result({"ok": False, "error": e.to_dict()})
    except Exception as e:  # keep the agent informed instead of dropping the session
        log.error("internal error: %s", traceback.format_exc())
        return _result({"ok": False, "error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}})


TargetName = Annotated[str, Field(description="Target name such as 'tb' (lowercase letters, digits, '-', '_'); must be inside the server allowlist")]


# ---------------------------------------------------------------- tools


@mcp.tool(
    title="List QNX targets",
    description="List the QEMU QNX targets this server may manage (directories under the targets dir that match the allowlist), "
    "with state running/stopped from the QEMU pid. Cheap, no SSH. Call first to discover target names.",
    annotations=RO,
)
def target_list() -> Annotated[CallToolResult, TargetList]:
    return _call(lambda: {"ok": True, "targets_dir": str(CFG.targets_dir), "allowlist": list(CFG.allow), "targets": list_targets(CFG)})


@mcp.tool(
    title="Create and boot a QNX target",
    description="Build a new QNX OS 8.0 QEMU image with mkqnximage in its own directory and boot it headless (KVM when available), "
    "then wait until root SSH works. Takes 1-3 minutes. Fails with target_exists if the name is taken. "
    f"Extra mkqnximage options are restricted to: {', '.join(sorted(MKQNXIMAGE_OPTIONS))} (form --opt=value). "
    "The image runs dumper, so crashing programs leave core files. Returns the target status.",
    annotations=MUT,
)
def target_create(
    name: TargetName,
    arch: Annotated[Literal["x86_64", "aarch64le"], Field(description="Target CPU architecture")] = "x86_64",
    ram: Annotated[str, Field(description="Guest RAM, e.g. 1G or 2048M")] = "1G",
    cpus: Annotated[int, Field(ge=1, le=4, description="Guest CPUs (1-4)")] = 2,
    options: Annotated[list[str] | None, Field(description="Extra mkqnximage options, e.g. ['--hostname=dev1', '--data-size=512']")] = None,
    boot_timeout_s: Annotated[float, Field(gt=0, le=900, description="Seconds to wait for SSH after boot")] = 180,
) -> Annotated[CallToolResult, TargetInfo]:
    return _call(lambda: {"ok": True, **Target(CFG, name, must_exist=False).create(arch=arch, ram=ram, cpus=cpus, options=options or [], boot_timeout=boot_timeout_s)})


@mcp.tool(
    title="Start a QNX target",
    description="Boot an existing target (no rebuild) and wait until root SSH answers. Idempotent: if it is already running it only waits for SSH. "
    "The SSH host key is re-pinned on each boot.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def target_start(
    name: TargetName,
    boot_timeout_s: Annotated[float, Field(gt=0, le=900, description="Seconds to wait for SSH")] = 120,
) -> Annotated[CallToolResult, TargetInfo]:
    return _call(lambda: {"ok": True, **Target(CFG, name).start(boot_timeout_s)})


@mcp.tool(
    title="Stop a QNX target",
    description="Power off the target's QEMU (mkqnximage --stop, then SIGTERM/SIGKILL of the verified QEMU pid). Running programs are lost; files on the target disk persist.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False),
)
def target_stop(name: TargetName) -> Annotated[CallToolResult, TargetInfo]:
    return _call(lambda: {"ok": True, **Target(CFG, name).stop()})


@mcp.tool(
    title="QNX target status",
    description="State of one target: running/stopped/booting, IP, arch, RAM, CPUs and, when running, an SSH probe with uname, the target clock and whether dumper is running. "
    "Use it to check readiness before deploy_and_run, and after errors such as target_down or ssh_failed.",
    annotations=RO,
)
def target_status(name: TargetName) -> Annotated[CallToolResult, TargetInfo]:
    return _call(lambda: {"ok": True, **Target(CFG, name).status(probe=True)})


@mcp.tool(
    title="Build for QNX",
    description="Compile on the build host with the QNX SDP 8.0 toolchain. system=auto picks cmake (CMakeLists.txt), make (Makefile) or qcc (plain sources). "
    "qcc mode builds the given sources (default: every *.c/*.cpp in path) with -g -O0 -Wall unless cflags is set. "
    "Returns diagnostics[] with file, line, column, severity, message (from gcc's JSON diagnostics when qcc is driven directly, "
    "parsed text for make/cmake), linker errors, and artifacts[] with path, arch and debug_info for deploy_and_run. "
    "ok=false with error.code build_failed when compilation fails.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def build(
    path: Annotated[str, Field(description="Project directory or a single source file on the build host")],
    system: Annotated[Literal["auto", "qcc", "make", "cmake"], Field(description="Build driver")] = "auto",
    arch: Annotated[Literal["x86_64", "aarch64le"], Field(description="Target architecture; must match the target you deploy to")] = "x86_64",
    sources: Annotated[list[str] | None, Field(description="qcc mode: source files (relative to path or absolute)")] = None,
    output: Annotated[str | None, Field(description="qcc mode: output executable path (default: first source's stem inside path)")] = None,
    cflags: Annotated[list[str] | None, Field(description="Compiler flags; qcc default is ['-g','-O0','-Wall']")] = None,
    ldflags: Annotated[list[str] | None, Field(description="qcc mode: linker flags/libraries, e.g. ['-lsocket']")] = None,
    make_args: Annotated[list[str] | None, Field(description="Extra make or cmake --build arguments")] = None,
    timeout_s: Annotated[float, Field(gt=0, le=3600)] = 300,
) -> Annotated[CallToolResult, BuildResult]:
    return _call(lambda: sdk.build(CFG, path, system, arch, sources, output, cflags, ldflags, make_args, timeout_s))


@mcp.tool(
    title="Deploy and run on a QNX target",
    description="Copy a host-built QNX executable to the target (/tmp/qnx-mcp/bin) and run it once via the qnx-mcp-run helper: own process group, stdin /dev/null, "
    "stdout and stderr captured separately (capped), wall-clock timeout that kills the whole process group. "
    "Returns outcome (exited | crashed | killed | timeout | exec_failed), exit_code or signal_name, and for crashes the exact core_path, "
    "obtained from dumper's DCMD_DUMPER_GETPATH after its completion pulse. Checks the ELF arch and loader first (arch_mismatch, not_a_qnx_binary). "
    "Arguments are passed as an array and quoted, never interpreted by a shell.",
    annotations=MUT,
)
def deploy_and_run(
    target: TargetName,
    binary: Annotated[str, Field(description="Executable on the build host, usually build.artifacts[].path")],
    args: Annotated[list[str] | None, Field(description="Program arguments, one element per argv entry")] = None,
    timeout_s: Annotated[float, Field(gt=0, le=3600, description="Kill the program after this many seconds")] = 30,
    env: Annotated[dict[str, str] | None, Field(description="Extra environment variables for the program")] = None,
    cwd: Annotated[str | None, Field(description="Working directory on the target")] = None,
    max_output_kb: Annotated[int, Field(ge=1, le=4096, description="Cap for each of stdout and stderr")] = 64,
    core_wait_s: Annotated[float, Field(ge=0, le=300, description="How long to wait for dumper to finish a core")] = 15,
) -> Annotated[CallToolResult, RunResult]:
    return _call(lambda: ops.deploy_and_run(CFG, target, binary, args, timeout_s, env, cwd, max_output_kb, core_wait_s))


@mcp.tool(
    title="Analyse a QNX crash",
    description="Fetch the core file the QNX dumper wrote for a crashed run, open it with the SDP's ntox86_64-gdb/ntoaarch64-gdb (GDB/MI, batch, target sysroot) "
    "and return a structured source-level backtrace: frames with function, file, line, address, library, locals and the surrounding source lines, "
    "the signal, si_code and fault address (0x0 = NULL dereference), the faulting instruction and a one-line summary. "
    "By default uses the latest crashed run on the target; pass run_id from deploy_and_run, or core_path plus binary for any other core.",
    annotations=RO,
)
def get_crash(
    target: TargetName,
    run_id: Annotated[str | None, Field(description="run_id returned by deploy_and_run")] = None,
    core_path: Annotated[str | None, Field(description="Core file on the target, e.g. /data/var/dumper/app.core")] = None,
    binary: Annotated[str | None, Field(description="Unstripped executable on the build host matching the core")] = None,
    max_frames: Annotated[int, Field(ge=1, le=256)] = 32,
    locals_frames: Annotated[int, Field(ge=0, le=16, description="Collect local variables for this many innermost frames")] = 3,
) -> Annotated[CallToolResult, CrashReport]:
    return _call(lambda: ops.get_crash(CFG, target, run_id, core_path, binary, max_frames, locals_frames))


@mcp.tool(
    title="List processes on a QNX target",
    description="Run pidin with fixed -F format strings and return processes as JSON: pid, ppid, uid, name, path, args, code/data size, "
    "thread count and thread states (READY, RECEIVE, REPLY, SEND, MUTEX, ...). include_threads adds per-thread priority, policy, state and what it is blocked on.",
    annotations=RO,
)
def processes(
    target: TargetName,
    name: Annotated[str | None, Field(description="Case-insensitive substring filter on process name or path")] = None,
    pid: Annotated[int | None, Field(description="Only this pid")] = None,
    include_threads: Annotated[bool, Field(description="Include the per-thread list")] = False,
) -> Annotated[CallToolResult, ProcessList]:
    return _call(lambda: ops.processes(CFG, target, name, pid, include_threads))


@mcp.tool(
    title="Read slog2 logs on a QNX target",
    description="Read the system log (slog2info -f -s -t) and filter it: buffer (buffer or buffer-set name), process (name substring or pid), "
    "since_s (relative to the target clock), min_severity (keep this level and more severe), contains (substring). Returns the newest `limit` entries with timestamps, pid, thread, severity and message.",
    annotations=RO,
)
def logs(
    target: TargetName,
    buffer: Annotated[str | None, Field(description="Buffer name or buffer-set name")] = None,
    process: Annotated[str | None, Field(description="Process name substring or numeric pid")] = None,
    since_s: Annotated[float | None, Field(gt=0, description="Only entries from the last N seconds of target time")] = None,
    min_severity: Annotated[Literal["shutdown", "critical", "error", "warning", "notice", "info", "debug1", "debug2"] | None, Field(description="Keep entries at this severity or more severe")] = None,
    contains: Annotated[str | None, Field(description="Case-insensitive substring of the message")] = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> Annotated[CallToolResult, LogResult]:
    return _call(lambda: ops.logs(CFG, target, buffer, process, since_s, min_severity, contains, limit))


@mcp.tool(
    title="Check the QNX host environment",
    description="Prerequisite checks with fixes: QNX SDP environment, qcc, mkqnximage, gdb, KVM access, QEMU, ssh/scp, SSH key, DHCP lease file, targets directory. Run it first on a new machine.",
    annotations=RO,
)
def environment_check() -> Annotated[CallToolResult, EnvCheck]:
    return _call(lambda: ops.environment_check(CFG))


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"qnx-mcp {__version__}")
        return
    mcp.run()


if __name__ == "__main__":
    main()
