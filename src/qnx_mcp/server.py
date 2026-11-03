# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""qnx-mcp: MCP server (stdio) with structured tools for QNX OS 8.0 development."""

from __future__ import annotations

import copy
import json
import logging
import signal
import sys
import traceback
from typing import Annotated, Any, Callable, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__, crash, ops, sdk
from .target import MKQNXIMAGE_OPTIONS, Target, list_targets
from .util import CURRENT_CALL, CallContext, Config, QnxMcpError, kill_all_children, private_dir

log = logging.getLogger("qnx_mcp")

INSTRUCTIONS = """\
qnx-mcp gives you structured, bounded access to QNX OS 8.0 targets (QEMU virtual machines
built with mkqnximage, reachable over SSH) and to the QNX SDP 8.0 toolchain on the build host.

Typical loop:
  1. target_list -> target_start (or target_create for a new VM) -> target_status.
  2. build (qcc/make/cmake) -> read diagnostics[] (file, line, column, severity, message), fix, rebuild.
  3. deploy_and_run with args and a timeout -> outcome is exited | crashed | killed | timeout | stopped | exec_failed.
  4. outcome == crashed -> get_crash(run_id) -> source-level backtrace of every thread, with locals and source lines.
  5. Services and long programs: run_start -> run_poll (pass back stdout_next/stderr_next) -> run_stop.
  6. processes (pidin as JSON) and logs (slog2info, filtered) inspect the system, also while a run is active.
  7. file_put / file_get move fixtures and results (sizes and SHA-256 are verified).
Every tool returns JSON with ok=true, or ok=false plus error {code, message, hint}; follow the hint.
Paths in build/deploy/get_crash/file_* arguments marked "host" are paths on the build host, not on the target.
Never assume a target is yours unless target_list shows it; the server refuses names outside its allowlist.
"""


class QnxMCPServer(MCPServer):
    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as e:
            # Arguments that fail the input schema get the same structured error as every other failure.
            if isinstance(e.__cause__, ValidationError) and self._tool_manager.get_tool(name) is not None:
                errors = [{"field": ".".join(str(p) for p in err["loc"]), "problem": err["msg"]} for err in e.__cause__.errors()]
                return _result({"ok": False, "error": {"code": "invalid_argument", "message": f"arguments for {name} do not match its input schema",
                                                       "hint": "Fix the fields listed in details.errors (see the tool's inputSchema).", "details": {"errors": errors}}})
            raise


mcp = QnxMCPServer("qnx-mcp", instructions=INSTRUCTIONS, version=__version__)
CFG = Config.from_env()


def _ann(read_only: bool, destructive: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive, idempotentHint=idempotent, openWorldHint=False)


RO = _ann(True, False, True)


# ---------------------------------------------------------------- output schemas


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorInfo(_M):
    code: str = Field(description="Stable code: invalid_argument, path_not_allowed, target_not_found, target_not_allowed, target_exists, target_down, "
                      "target_busy, ssh_failed, host_key_mismatch, timeout, cancelled, sdk_missing, tool_missing, ssh_key_missing, mkqnximage_failed, "
                      "build_failed, arch_mismatch, not_a_qnx_binary, deploy_failed, copy_failed, runner_failed, remote_failed, core_not_found, "
                      "core_too_large, core_mismatch, gdb_failed, file_exists, file_not_found, file_too_large, internal_error")
    message: str
    hint: str | None = Field(None, description="What to do next")
    details: dict[str, Any] | None = None


class ToolErrorResult(_M):
    ok: Literal[False]
    error: ErrorInfo


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
    host_key_policy: Literal["image", "first_use"] = Field(description="image: keys mkqnximage generated on the host are pinned; first_use: pinned at first contact")
    ssh: Any = Field(None, description="'ok' or an error object from the SSH probe")
    uname: str | None = None
    target_time: int | None = Field(None, description="Target clock, Unix seconds")
    dumper_running: bool | None = Field(None, description="Whether /proc/dumper exists (cores will be written)")
    dumper_args: str | None = None
    core_naming: Literal["sequential", "fixed"] | None = Field(None, description="sequential when dumper runs with -n (app.1.core, app.2.core, ...)")
    was_running: bool | None = None


class TargetList(_M):
    ok: bool
    targets_dir: str
    allowlist: list[str]
    targets: list[TargetInfo]


class DestroyResult(_M):
    ok: bool
    name: str
    destroyed: bool
    was_running: bool
    detail: str


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
    build_id: str | None = None
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


class RunFields(_M):
    pid: int
    exit_code: int | None
    signal: int | None
    signal_name: str | None
    exec_error: str | None
    timed_out: bool
    disconnected: bool = Field(description="The ssh session ended (client cancelled); the program was stopped")
    runner_stopped_by: str | None
    duration_ms: int
    group_clean: bool = Field(description="No process of the program's process group survived the run")
    capture_complete: bool = Field(description="False if something outside the process group kept the output pipes open")
    stdout_bytes: int
    stdout_truncated: bool
    stderr_bytes: int
    stderr_truncated: bool
    dumper: str = Field(description="registered/notified/notified_no_path/timeout/unavailable/not_applicable/error")
    dumper_error: str | None
    core_path: str | None = Field(description="Core path on the target (moved into the run's directory)")
    dumper_core_path: str | None = Field(description="Path dumper reported through DCMD_DUMPER_GETPATH")
    core_move_error: str | None
    warnings: list[dict[str, Any]]


class RunResult(RunFields):
    ok: bool
    run_id: str = Field(description="Pass to get_crash")
    target: str
    outcome: Literal["exited", "crashed", "killed", "timeout", "stopped", "exec_failed"]
    binary: str
    binary_sha256: str
    build_id: str | None
    remote_path: str
    args: list[str]
    stdout: str
    stderr: str
    next_step: str | None = None


class RunStartResult(_M):
    ok: bool
    run_id: str
    target: str
    state: Literal["running"]
    pid: int | None
    runner_pid: int | None
    binary: str
    binary_sha256: str
    remote_path: str
    args: list[str]
    deadline_s: float
    next_step: str


class RunPollResult(_M):
    ok: bool
    run_id: str
    target: str
    state: Literal["running", "finished", "lost"]
    alive: bool
    pid: int | None
    stdout: str
    stdout_offset: int
    stdout_next: int = Field(description="Pass as stdout_offset next time")
    stdout_size: int | None
    stderr: str
    stderr_offset: int
    stderr_next: int
    stderr_size: int | None
    more_output: bool
    outcome: Literal["exited", "crashed", "killed", "timeout", "stopped", "exec_failed"] | None = None
    result: dict[str, Any] | None = None
    next_step: str | None = None
    note: str | None = None


class RunStopResult(_M):
    ok: bool
    run_id: str
    target: str
    was_running: bool
    forced: bool
    state: Literal["running", "finished", "lost"]
    outcome: Literal["exited", "crashed", "killed", "timeout", "stopped", "exec_failed"] | None
    result: dict[str, Any] | None


class FilePutResult(_M):
    ok: bool
    target: str
    local_path: str
    remote_path: str
    size: int
    sha256: str
    mode: str
    replaced: bool


class FileGetResult(_M):
    ok: bool
    target: str
    remote_path: str
    local_path: str
    size: int
    sha256: str
    mode: str | None
    text: str | None = Field(None, description="File content when it is UTF-8 text of at most 64 KiB")


class SourceLine(_M):
    line: int
    text: str
    current: bool


class Frame(_M):
    level: int | None
    function: str | None
    file: str | None
    fullname: str | None
    line: int | None
    address: str | None
    library: str | None
    user_code: bool = Field(description="True when the frame's source file exists on the build host outside the SDP")
    source: list[SourceLine] | None = None
    locals: list[dict[str, Any]] | None = None


class Provenance(_M):
    source: Literal["run_record", "manual"]
    binary_sha256: str
    binary_build_id: str | None
    build_id_in_core: bool | None = Field(description="The binary's GNU build ID appears in the core's notes")
    deployed_path_in_core: bool | None = Field(description="The content-addressed path the run used appears in the core's notes")
    core_sha256: str
    core_size: int
    core_pid: int | None = Field(None, description="Process id recorded in the core (from gdb's thread list)")
    pid_matches_run: bool | None = Field(None, description="core_pid equals the pid of the run that crashed")
    verified: bool


class CrashReport(_M):
    ok: bool
    target: str
    run_id: str | None
    summary: str
    signal: int | None
    signal_name: str | None
    signal_description: str | None
    si_code: int | None
    si_code_name: str | None
    fault_address: str | None
    faulting_instruction: str | None
    faulting_thread_id: str | None
    faulting_frame: Frame = Field(description="Innermost frame of the faulting thread: where the signal happened (may be in a library)")
    user_frame: Frame | None = Field(description="Innermost frame in your own source code, the usual place to fix")
    fault_in_user_code: bool
    frames: list[Frame]
    threads: list[dict[str, Any]] = Field(description="Every thread with its state and up to 16 frames; faulting=true marks the faulting thread")
    provenance: Provenance
    core_path_target: str
    core_path_local: str
    binary: str
    binary_original: str
    gdb_warnings: list[str]


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
    source_truncated: bool
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


async def _acall(fn: Callable[[], dict[str, Any]]) -> CallToolResult:
    """Run a blocking tool body in a worker thread. If the client cancels the request, every host
    process the call started is killed (ssh, scp, gdb, mkqnximage), which also ends the target-side
    runner (it watches its stdin), and the worker then unwinds and releases its locks."""
    ctx = CallContext()

    def work() -> CallToolResult:
        token = CURRENT_CALL.set(ctx)
        try:
            return _call(fn)
        finally:
            CURRENT_CALL.reset(token)

    try:
        return await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
    except anyio.get_cancelled_exc_class():
        ctx.cancel()
        log.info("call cancelled by the client; killed its host processes")
        raise


TargetName = Annotated[str, Field(description="Target name such as 'tb' (lowercase letters, digits, '-', '_'); must be inside the server allowlist")]
RunId = Annotated[str, Field(description="run_id returned by run_start (r- and 12 hex digits)")]
EnvVars = Annotated[dict[str, str] | None, Field(description="Extra environment variables for the program")]


# ---------------------------------------------------------------- tools: targets


@mcp.tool(
    title="List QNX targets",
    description="List the QEMU QNX targets this server may manage (directories under the targets dir that match the allowlist), "
    "with state running/stopped from the QEMU pid. Cheap, no SSH. Call first to discover target names.",
    annotations=RO,
)
async def target_list() -> Annotated[CallToolResult, TargetList]:
    return await _acall(lambda: {"ok": True, "targets_dir": str(CFG.targets_dir), "allowlist": list(CFG.allow), "targets": list_targets(CFG)})


@mcp.tool(
    title="Create and boot a QNX target",
    description="Build a new QNX OS 8.0 QEMU image with mkqnximage in its own directory and boot it headless (KVM when available), "
    "then wait until root SSH works. Takes 20 s to 3 minutes. Fails with target_exists if the name is taken; a failed build or boot "
    "is cleaned up (QEMU stopped, directory removed, mkqnximage log kept). The SSH host keys mkqnximage generates are pinned. "
    f"Extra mkqnximage options are restricted to: {', '.join(sorted(MKQNXIMAGE_OPTIONS))} (form --opt=value). "
    "The image runs dumper with sequential core names (unless --secpol is set), so crashing programs leave distinct core files.",
    annotations=_ann(False, False, False),
)
async def target_create(
    name: TargetName,
    arch: Annotated[Literal["x86_64", "aarch64le"], Field(description="Target CPU architecture")] = "x86_64",
    ram: Annotated[str, Field(description="Guest RAM, e.g. 1G or 2048M")] = "1G",
    cpus: Annotated[int, Field(ge=1, le=4, description="Guest CPUs (1-4)")] = 2,
    options: Annotated[list[str] | None, Field(description="Extra mkqnximage options, e.g. ['--hostname=dev1', '--data-size=512']")] = None,
    boot_timeout_s: Annotated[float, Field(gt=0, le=900, description="Seconds to wait for SSH after boot")] = 180,
) -> Annotated[CallToolResult, TargetInfo]:
    return await _acall(lambda: {"ok": True, **Target(CFG, name, must_exist=False).create(arch=arch, ram=ram, cpus=cpus, options=options or [], boot_timeout=boot_timeout_s)})


@mcp.tool(
    title="Start a QNX target",
    description="Boot an existing target (no rebuild) and wait until root SSH answers with the target's pinned host key. "
    "Idempotent: if it is already running it only waits for SSH.",
    annotations=_ann(False, False, True),
)
async def target_start(
    name: TargetName,
    boot_timeout_s: Annotated[float, Field(gt=0, le=900, description="Seconds to wait for SSH")] = 120,
) -> Annotated[CallToolResult, TargetInfo]:
    return await _acall(lambda: {"ok": True, **Target(CFG, name).start(boot_timeout_s)})


@mcp.tool(
    title="Stop a QNX target",
    description="Power off the target's QEMU (mkqnximage --stop, then SIGTERM/SIGKILL through a pidfd of the verified QEMU process). "
    "Waits for runs in progress (they hold a shared lock). Background runs are lost; files on the target disk persist.",
    annotations=_ann(False, True, True),
)
async def target_stop(name: TargetName) -> Annotated[CallToolResult, TargetInfo]:
    return await _acall(lambda: {"ok": True, **Target(CFG, name).stop()})


@mcp.tool(
    title="Destroy a QNX target",
    description="Stop and permanently delete a target this server manages: its mkqnximage directory under the targets dir and its server state. "
    "Refuses names outside the allowlist, symlinks and anything that is not an mkqnximage directory directly inside the targets dir. "
    "confirm must repeat the target name.",
    annotations=_ann(False, True, True),
)
async def target_destroy(
    name: TargetName,
    confirm: Annotated[str, Field(description="Must equal name, to confirm the deletion")],
) -> Annotated[CallToolResult, DestroyResult]:
    def go() -> dict[str, Any]:
        if confirm != name:
            raise QnxMcpError("invalid_argument", "confirm does not match name", hint="Pass confirm equal to the target name to delete it.")
        return {"ok": True, **Target(CFG, name).destroy()}
    return await _acall(go)


@mcp.tool(
    title="QNX target status",
    description="State of one target: running/stopped/booting, IP, arch, RAM, CPUs, host key policy and, when running, an SSH probe with uname, "
    "the target clock, whether dumper is running and with which core naming. Use it before deploy_and_run and after target_down or ssh_failed errors.",
    annotations=RO,
)
async def target_status(name: TargetName) -> Annotated[CallToolResult, TargetInfo]:
    return await _acall(lambda: {"ok": True, **Target(CFG, name).status(probe=True)})


# ---------------------------------------------------------------- tools: build and run


@mcp.tool(
    title="Build for QNX",
    description="Compile on the build host with the QNX SDP 8.0 toolchain. system=auto picks cmake (CMakeLists.txt), make (Makefile) or qcc (plain sources). "
    "make and cmake run the project's own build files, which can execute any command: only build trees you trust. "
    "qcc mode builds the given sources (default: every *.c/*.cpp in path) with -g -O0 -Wall unless cflags is set. "
    "Returns diagnostics[] with file, line, column, severity, message (gcc JSON diagnostics in qcc mode, parsed text for make/cmake), "
    "linker errors, and artifacts[] with path, arch, build_id and debug_info for deploy_and_run. ok=false with error.code build_failed when compilation fails.",
    annotations=_ann(False, True, False),
)
async def build(
    path: Annotated[str, Field(description="Host path: project directory or a single source file")],
    system: Annotated[Literal["auto", "qcc", "make", "cmake"], Field(description="Build driver")] = "auto",
    arch: Annotated[Literal["x86_64", "aarch64le"], Field(description="Target architecture; must match the target you deploy to")] = "x86_64",
    sources: Annotated[list[str] | None, Field(description="qcc mode: source files (relative to path or absolute)")] = None,
    output: Annotated[str | None, Field(description="qcc mode: output executable path (default: first source's stem inside path)")] = None,
    cflags: Annotated[list[str] | None, Field(description="Compiler flags; qcc default is ['-g','-O0','-Wall']")] = None,
    ldflags: Annotated[list[str] | None, Field(description="qcc mode: linker flags/libraries, e.g. ['-lsocket']")] = None,
    make_args: Annotated[list[str] | None, Field(description="Extra make or cmake --build arguments")] = None,
    timeout_s: Annotated[float, Field(gt=0, le=3600)] = 300,
) -> Annotated[CallToolResult, BuildResult]:
    return await _acall(lambda: sdk.build(CFG, path, system, arch, sources, output, cflags, ldflags, make_args, timeout_s))


@mcp.tool(
    title="Deploy and run on a QNX target",
    description="Snapshot a host-built QNX executable, copy it to the target (content-addressed under /tmp/qnx-mcp/apps, SHA-256 verified on the target) "
    "and run it once via the qnx-mcp-run helper: own process group, stdin /dev/null, stdout and stderr captured separately (capped, truncation reported), "
    "wall-clock timeout; when the program ends the rest of its process group is killed too. "
    "Returns outcome (exited | crashed | killed | timeout | stopped | exec_failed), exit_code or signal_name, and for crashes the exact core_path "
    "obtained from dumper (DCMD_DUMPER_NOTIFYEVENT pulse, then DCMD_DUMPER_GETPATH), moved into the run's own directory. "
    "Checks the ELF arch and loader first. Arguments are an array, never interpreted by a shell. For long-running programs use run_start.",
    annotations=_ann(False, True, False),
)
async def deploy_and_run(
    target: TargetName,
    binary: Annotated[str, Field(description="Host path of the executable, usually build.artifacts[].path")],
    args: Annotated[list[str] | None, Field(description="Program arguments, one element per argv entry")] = None,
    timeout_s: Annotated[float, Field(gt=0, le=3600, description="Kill the program after this many seconds")] = 30,
    env: EnvVars = None,
    cwd: Annotated[str | None, Field(description="Working directory on the target")] = None,
    max_output_kb: Annotated[int, Field(ge=1, le=4096, description="Cap for each of stdout and stderr")] = 64,
    core_wait_s: Annotated[float, Field(ge=0, le=300, description="How long to wait for dumper to finish a core")] = 15,
) -> Annotated[CallToolResult, RunResult]:
    return await _acall(lambda: ops.deploy_and_run(CFG, target, binary, args, timeout_s, env, cwd, max_output_kb, core_wait_s))


@mcp.tool(
    title="Start a long-running program on a QNX target",
    description="Like deploy_and_run, but the program runs in the background (its own session on the target) so the call returns at once. "
    "Output goes to files on the target (capped per stream); read it with run_poll using offsets, end it with run_stop. "
    "timeout_s is a hard limit on its lifetime. processes and logs keep working while it runs. Crashes leave a core for get_crash(run_id).",
    annotations=_ann(False, True, False),
)
async def run_start(
    target: TargetName,
    binary: Annotated[str, Field(description="Host path of the executable")],
    args: Annotated[list[str] | None, Field(description="Program arguments, one element per argv entry")] = None,
    timeout_s: Annotated[float, Field(gt=0, le=86400, description="Kill the program after this many seconds")] = 600,
    env: EnvVars = None,
    cwd: Annotated[str | None, Field(description="Working directory on the target")] = None,
    max_output_kb: Annotated[int, Field(ge=1, le=65536, description="Cap for each of the stdout and stderr files")] = 1024,
    core_wait_s: Annotated[float, Field(ge=0, le=300, description="How long to wait for dumper to finish a core")] = 15,
) -> Annotated[CallToolResult, RunStartResult]:
    return await _acall(lambda: ops.run_start(CFG, target, binary, args, timeout_s, env, cwd, max_output_kb, core_wait_s))


@mcp.tool(
    title="Poll a background run",
    description="Read new output of a run_start run from byte offsets (pass back stdout_next and stderr_next) and its state: running, finished "
    "(with outcome and the final result) or lost (target rebooted). wait_s waits up to that long for new output or the end. "
    "Only runs started by this server are accessible.",
    annotations=RO,
)
async def run_poll(
    target: TargetName,
    run_id: RunId,
    stdout_offset: Annotated[int, Field(ge=0)] = 0,
    stderr_offset: Annotated[int, Field(ge=0)] = 0,
    max_kb: Annotated[int, Field(ge=1, le=4096, description="Maximum bytes returned per stream")] = 64,
    wait_s: Annotated[float, Field(ge=0, le=60)] = 0,
) -> Annotated[CallToolResult, RunPollResult]:
    return await _acall(lambda: ops.run_poll(CFG, target, run_id, stdout_offset, stderr_offset, max_kb, wait_s))


@mcp.tool(
    title="Stop a background run",
    description="Stop a run_start run: SIGTERM to its runner, which kills the program's process group and writes the final status; "
    "after grace_s the group and the runner are killed outright. Returns the final outcome.",
    annotations=_ann(False, True, True),
)
async def run_stop(
    target: TargetName,
    run_id: RunId,
    grace_s: Annotated[float, Field(ge=0, le=60)] = 5,
) -> Annotated[CallToolResult, RunStopResult]:
    return await _acall(lambda: ops.run_stop(CFG, target, run_id, grace_s))


@mcp.tool(
    title="Analyse a QNX crash",
    description="Fetch the core file of a crashed run (size and SHA-256 verified), check that it belongs to the exact binary that was deployed "
    "(GNU build ID or the content-addressed path in the core's notes; error core_mismatch otherwise), open it with the SDP's gdb in MI mode and return "
    "a structured backtrace: faulting_frame (where the signal happened) and user_frame (innermost frame in your sources), frames with function, file, "
    "line, address, library, locals and source lines, every thread's stack, signal, si_code, fault address (0x0 = NULL dereference), the faulting "
    "instruction and a one-line summary. Default: the newest crashed run on the target; or pass run_id, or core_path plus binary for any other core.",
    annotations=RO,
)
async def get_crash(
    target: TargetName,
    run_id: Annotated[str | None, Field(description="run_id returned by deploy_and_run or run_start")] = None,
    core_path: Annotated[str | None, Field(description="Core file on the target, for a core not produced through qnx-mcp")] = None,
    binary: Annotated[str | None, Field(description="Host path of the unstripped executable matching core_path")] = None,
    max_frames: Annotated[int, Field(ge=1, le=256)] = 32,
    locals_frames: Annotated[int, Field(ge=0, le=16, description="Collect local variables for this many innermost frames")] = 3,
    max_core_mb: Annotated[int, Field(ge=1, le=4096, description="Refuse larger cores (also applies after decompression)")] = 512,
) -> Annotated[CallToolResult, CrashReport]:
    return await _acall(lambda: crash.get_crash(CFG, target, run_id, core_path, binary, max_frames, locals_frames, max_core_mb))


# ---------------------------------------------------------------- tools: files and inspection


@mcp.tool(
    title="Copy a file to a QNX target",
    description="Copy one host file to the target (below /tmp or /data, not into qnx-mcp's own /tmp/qnx-mcp) through a temporary name, "
    "verify size, mode and SHA-256 on the target, then rename it into place. Refuses to replace an existing file unless overwrite=true.",
    annotations=_ann(False, True, False),
)
async def file_put(
    target: TargetName,
    local_path: Annotated[str, Field(description="Host path of the file to copy")],
    remote_path: Annotated[str, Field(description="Absolute destination on the target, e.g. /tmp/app/config.json")],
    mode: Annotated[str, Field(description="Octal permissions, e.g. 0644 or 0755")] = "0644",
    overwrite: bool = False,
    max_mb: Annotated[int, Field(ge=1, le=1024)] = 64,
) -> Annotated[CallToolResult, FilePutResult]:
    return await _acall(lambda: ops.file_put(CFG, target, local_path, remote_path, mode, overwrite, max_mb))


@mcp.tool(
    title="Copy a file from a QNX target",
    description="Copy one regular file from the target to the build host, verifying size and SHA-256. Without local_path it lands in the server's "
    "state directory (path returned); with local_path it never follows or replaces a symlink and replaces a file only with overwrite=true. "
    "UTF-8 files up to 64 KiB are also returned inline as text.",
    annotations=_ann(False, False, False),
)
async def file_get(
    target: TargetName,
    remote_path: Annotated[str, Field(description="Absolute path of the file on the target")],
    local_path: Annotated[str | None, Field(description="Host destination (absolute); default: a new file in the server state dir")] = None,
    overwrite: bool = False,
    max_mb: Annotated[int, Field(ge=1, le=1024)] = 16,
) -> Annotated[CallToolResult, FileGetResult]:
    return await _acall(lambda: ops.file_get(CFG, target, remote_path, local_path, overwrite, max_mb))


@mcp.tool(
    title="List processes on a QNX target",
    description="Run pidin with fixed -F format strings and return processes as JSON: pid, ppid, uid, name, path, args, code/data size, "
    "thread count and thread states (READY, RECEIVE, REPLY, SEND, MUTEX, ...). include_threads adds per-thread priority, policy, state and what it is blocked on.",
    annotations=RO,
)
async def processes(
    target: TargetName,
    name: Annotated[str | None, Field(description="Case-insensitive substring filter on process name or path")] = None,
    pid: Annotated[int | None, Field(description="Only this pid")] = None,
    include_threads: Annotated[bool, Field(description="Include the per-thread list")] = False,
) -> Annotated[CallToolResult, ProcessList]:
    return await _acall(lambda: ops.processes(CFG, target, name, pid, include_threads))


@mcp.tool(
    title="Read slog2 logs on a QNX target",
    description="Read the system log (slog2info -f -s -t) and filter it: buffer (buffer or buffer-set name), process (name substring or pid), "
    "since_s (relative to the target clock), min_severity (keep this level and more severe), contains (substring). Returns the newest `limit` entries with timestamps, pid, thread, severity and message.",
    annotations=RO,
)
async def logs(
    target: TargetName,
    buffer: Annotated[str | None, Field(description="Buffer name or buffer-set name")] = None,
    process: Annotated[str | None, Field(description="Process name substring or numeric pid")] = None,
    since_s: Annotated[float | None, Field(gt=0, description="Only entries from the last N seconds of target time")] = None,
    min_severity: Annotated[Literal["shutdown", "critical", "error", "warning", "notice", "info", "debug1", "debug2"] | None, Field(description="Keep entries at this severity or more severe")] = None,
    contains: Annotated[str | None, Field(description="Case-insensitive substring of the message")] = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> Annotated[CallToolResult, LogResult]:
    return await _acall(lambda: ops.logs(CFG, target, buffer, process, since_s, min_severity, contains, limit))


@mcp.tool(
    title="Check the QNX host environment",
    description="Prerequisite checks with fixes: QNX SDP environment, qcc, mkqnximage, gdb, KVM access, QEMU, ssh/scp, SSH key, DHCP lease file, "
    "target allowlist, targets directory, workspace confinement. Run it first on a new machine.",
    annotations=RO,
)
async def environment_check() -> Annotated[CallToolResult, EnvCheck]:
    return await _acall(lambda: ops.environment_check(CFG))


# ---------------------------------------------------------------- error envelope in every output schema


def _envelope_schemas() -> None:
    """Advertise each tool's output schema as 'success model OR error object', so results with
    isError=true also conform to the declared schema. Successes are still validated against the
    success model by the SDK before they are sent."""
    err = ToolErrorResult.model_json_schema()
    err_defs = err.pop("$defs", {})
    for tool in mcp._tool_manager.list_tools():
        fm = tool.fn_metadata
        if fm.output_schema is None:
            continue
        success = copy.deepcopy(fm.output_schema)
        defs = {**success.pop("$defs", {}), **err_defs, "ToolErrorResult": err}
        fm.output_schema = {"type": "object", "anyOf": [success, {"$ref": "#/$defs/ToolErrorResult"}], "$defs": defs}


_envelope_schemas()


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"qnx-mcp {__version__}")
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # runs atexit: no host child outlives the server
    try:
        private_dir(CFG.state_dir)  # cores, logs and run records stay readable by this user only
    except (OSError, QnxMcpError) as e:
        log.warning("state directory %s: %s", CFG.state_dir, e)
    try:
        mcp.run()
    finally:
        kill_all_children()


if __name__ == "__main__":
    main()
