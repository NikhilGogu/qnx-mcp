# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Errors, configuration, bounded subprocess execution, cancellation, locking and input validation."""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import dataclasses
import fcntl
import fnmatch
import os
import re
import shlex
import signal
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


class QnxMcpError(Exception):
    """An error reported to the agent as a structured object.

    `code` is a stable machine-readable identifier (documented in docs/DESIGN.md),
    `hint` tells the agent what to do next.
    """

    def __init__(self, code: str, message: str, hint: str | None = None, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.details:
            d["details"] = self.details
        return d


@dataclasses.dataclass(frozen=True)
class Config:
    sdp_env: Path
    targets_dir: Path
    state_dir: Path
    allow: tuple[str, ...]
    ssh_key: Path
    leases: Path
    workspace: tuple[Path, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        home = Path(env.get("HOME", str(Path.home())))
        base = Path(env.get("QNX_MCP_HOME", str(home / ".qnx-mcp")))
        # No default: an unset or empty allowlist allows nothing ("*" must be explicit).
        allow = tuple(p.strip() for p in env.get("QNX_MCP_ALLOW_TARGETS", "").split(",") if p.strip())
        workspace = tuple(Path(p).expanduser() for p in env.get("QNX_MCP_WORKSPACE", "").split(os.pathsep) if p.strip())
        return cls(
            sdp_env=Path(env.get("QNX_MCP_SDP_ENV", str(home / "qnx800" / "qnxsdp-env.sh"))),
            targets_dir=Path(env.get("QNX_MCP_TARGETS_DIR", str(base / "targets"))),
            state_dir=Path(env.get("QNX_MCP_STATE_DIR", str(base / "state"))),
            allow=allow,
            ssh_key=Path(env.get("QNX_MCP_SSH_KEY", str(home / ".ssh" / "id_ed25519"))),
            leases=Path(env.get("QNX_MCP_DHCP_LEASES", "/var/lib/libvirt/dnsmasq/virbr0.status")),
            workspace=workspace,
        )

    def allowed(self, name: str) -> bool:
        # fnmatchcase matches the whole name (the translated pattern is anchored with \Z).
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.allow)


# ---------------------------------------------------------------- validation

TARGET_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
# Remote paths are passed to scp, whose legacy protocol lets the remote shell see them,
# so they are restricted to characters that need no quoting at all.
REMOTE_PATH_RE = re.compile(r"/[A-Za-z0-9._+/-]{1,255}")
REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
RUN_ID_RE = re.compile(r"r-[0-9a-f]{12}")


def check_target_name(cfg: Config, name: str) -> str:
    if not isinstance(name, str) or not TARGET_NAME_RE.fullmatch(name):
        raise QnxMcpError(
            "invalid_argument",
            f"invalid target name {name!r}",
            hint="Use 1-32 characters: a lowercase letter followed by lowercase letters, digits, '-' or '_'.",
        )
    if not cfg.allow:
        raise QnxMcpError(
            "target_not_allowed",
            "this server has no target allowlist, so it refuses every target",
            hint="Set QNX_MCP_ALLOW_TARGETS in the server's environment (comma-separated globs such as 'dev*'; '*' allows every target in the targets dir).",
        )
    if not cfg.allowed(name):
        raise QnxMcpError(
            "target_not_allowed",
            f"target {name!r} is outside this server's allowlist {list(cfg.allow)}",
            hint="Use one of the allowed target names (QNX_MCP_ALLOW_TARGETS); other targets belong to someone else.",
        )
    return name


def check_remote_path(path: str, what: str = "remote path") -> str:
    parts = (path or "").split("/")
    if not isinstance(path, str) or not REMOTE_PATH_RE.fullmatch(path) or ".." in parts or "." in parts[1:]:
        raise QnxMcpError(
            "invalid_argument",
            f"unsafe {what} {path!r}",
            hint="Use an absolute path made of letters, digits and . _ + - / only, without '.' or '..' components.",
        )
    return path


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise QnxMcpError("invalid_argument", f"malformed run_id {run_id!r}", hint="Pass a run_id returned by deploy_and_run or run_start (r- and 12 hex digits).")
    return run_id


def within(path: Path, roots: Sequence[Path]) -> bool:
    real = os.path.realpath(path)
    for root in roots:
        r = os.path.realpath(root)
        if real == r or real.startswith(r.rstrip("/") + "/"):
            return True
    return False


def check_host_path(cfg: Config, path: str, what: str, *, kind: str = "file") -> Path:
    """Resolve a host path from the agent. With QNX_MCP_WORKSPACE set it must resolve inside it."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise QnxMcpError("invalid_argument", f"empty or invalid {what}")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise QnxMcpError("invalid_argument", f"{what} must be an absolute path on the build host: {path!r}")
    real = Path(os.path.realpath(p))
    if cfg.workspace and not within(real, cfg.workspace):
        raise QnxMcpError(
            "path_not_allowed",
            f"{what} {real} is outside the workspace {[str(w) for w in cfg.workspace]}",
            hint="Use a path inside QNX_MCP_WORKSPACE (symlinks are resolved before the check).",
        )
    if kind == "file" and not real.is_file():
        raise QnxMcpError("invalid_argument", f"{what} is not a file on the build host: {real}")
    if kind == "dir" and not real.is_dir():
        raise QnxMcpError("invalid_argument", f"{what} is not a directory on the build host: {real}")
    if kind == "any" and not real.exists():
        raise QnxMcpError("invalid_argument", f"{what} does not exist on the build host: {real}")
    return real


def quote_argv(argv: Sequence[str]) -> str:
    """Quote an argument vector for the remote POSIX shell (QNX sh): one word per element."""
    return " ".join(shlex.quote(str(a)) for a in argv)


# ---------------------------------------------------------------- private state files


def private_dir(path: Path) -> Path:
    """Create path (and missing parents) with mode 0700; refuse symlinks and foreign owners."""
    missing = []
    p = path
    while not os.path.lexists(p):
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        with contextlib.suppress(FileExistsError):
            os.mkdir(d, 0o700)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise QnxMcpError("internal_error", f"state path {path} is not a real directory", hint="Remove the symlink or file at that path.")
    if st.st_uid != os.getuid():
        raise QnxMcpError("internal_error", f"state directory {path} belongs to another user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomic write with mode 0600 through a unique temporary file in the same directory."""
    private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- cancellation


class CallContext:
    """Per tool call: the host processes it started, so a cancelled call can kill them."""

    def __init__(self) -> None:
        self.cancelled = False
        self.procs: set[subprocess.Popen] = set()
        self.lock = threading.Lock()

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            procs = list(self.procs)
        for p in procs:
            _kill_group(p)


CURRENT_CALL: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("qnx_mcp_call", default=None)
_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()


def check_cancelled() -> None:
    ctx = CURRENT_CALL.get()
    if ctx is not None and ctx.cancelled:
        raise QnxMcpError("cancelled", "the client cancelled this call", hint="Nothing was left running on the host; check target state with target_status if needed.")


def kill_all_children() -> None:
    """Server shutdown: no host process started by a tool call may outlive the server."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for p in procs:
        _kill_group(p)


atexit.register(kill_all_children)


# ---------------------------------------------------------------- processes


@dataclasses.dataclass
class ProcResult:
    argv: list[str]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_s: float
    stdout_total: int = 0
    stderr_total: int = 0

    @property
    def out(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace")

    @property
    def truncated(self) -> bool:
        return self.stdout_total > len(self.stdout) or self.stderr_total > len(self.stderr)


def _kill_group(p: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)


def _drain(stream: Any, cap: int, box: list) -> None:
    """Read a pipe to EOF, keeping at most cap bytes (the rest is counted, not stored)."""
    kept = bytearray()
    total = 0
    try:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if len(kept) < cap:
                kept += chunk[: cap - len(kept)]
    except (OSError, ValueError):
        pass
    box[0], box[1] = bytes(kept), total


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    input: bytes | None = None,
    log_path: Path | None = None,
    max_output: int = 8 << 20,
    hold_stdin: bool = False,
) -> ProcResult:
    """Run argv (never through a shell) with a hard timeout and bounded output capture.

    The child gets its own session so that on timeout or cancellation the whole process
    group (ssh, make, compilers...) is killed. Each of stdout and stderr keeps at most
    max_output bytes; the rest is drained and counted (ProcResult.truncated). With
    log_path, output goes to that file instead (for mkqnximage --run, which leaves QEMU
    running in the background). hold_stdin keeps the child's stdin open until it exits,
    so a remote qnx-mcp-run -S notices when this process kills ssh.
    """
    check_cancelled()
    argv = [str(a) for a in argv]
    start = time.monotonic()
    log_f = open(log_path, "ab") if log_path else None
    ctx = CURRENT_CALL.get()
    try:
        try:
            p = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if (input is not None or hold_stdin) else subprocess.DEVNULL,
                stdout=log_f if log_f else subprocess.PIPE,
                stderr=subprocess.STDOUT if log_f else subprocess.PIPE,
                env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd else None,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise QnxMcpError("tool_missing", f"executable not found: {argv[0]}", hint="Check PATH and the QNX SDP installation.") from e
        with _ACTIVE_LOCK:
            _ACTIVE.add(p)
        if ctx is not None:
            with ctx.lock:
                ctx.procs.add(p)
                if ctx.cancelled:
                    _kill_group(p)
        boxes: list[list] = []
        threads: list[threading.Thread] = []
        for stream in (p.stdout, p.stderr):
            box: list = [b"", 0]
            boxes.append(box)
            if stream is not None:
                th = threading.Thread(target=_drain, args=(stream, max_output, box), daemon=True)
                th.start()
                threads.append(th)
        if p.stdin is not None:
            if input is not None:
                def feed() -> None:
                    with contextlib.suppress(OSError, ValueError):
                        p.stdin.write(input)
                        p.stdin.flush()
                    if not hold_stdin:
                        with contextlib.suppress(OSError, ValueError):
                            p.stdin.close()
                threading.Thread(target=feed, daemon=True).start()
        timed_out = False
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p)
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)
        if p.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                p.stdin.close()
        # A grandchild may hold the pipes after the child exits; do not wait for it forever.
        for th in threads:
            th.join(timeout=2)
        out, out_total = boxes[0][0], boxes[0][1]
        err, err_total = (boxes[1][0], boxes[1][1]) if len(boxes) > 1 else (b"", 0)
        res = ProcResult(argv, None if timed_out else p.returncode, out, err, timed_out, time.monotonic() - start, out_total, err_total)
    finally:
        if log_f:
            log_f.close()
    with _ACTIVE_LOCK:
        _ACTIVE.discard(p)
    if ctx is not None:
        with ctx.lock:
            ctx.procs.discard(p)
    check_cancelled()
    return res


def tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def wait_until(pred: Callable[[], bool], timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if pred():
            return True
        if time.monotonic() >= deadline:
            return False
        check_cancelled()
        time.sleep(interval)


# ---------------------------------------------------------------- locking


@contextlib.contextmanager
def file_lock(path: Path, *, exclusive: bool, timeout: float, what: str) -> Iterator[None]:
    """flock-based lock shared by every qnx-mcp process on this host."""
    private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise QnxMcpError(
                        "target_busy",
                        f"{what} is locked by another operation",
                        hint="Another tool call (possibly from another agent) is using this target; retry shortly.",
                        waited_s=timeout,
                    ) from None
                check_cancelled()
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
