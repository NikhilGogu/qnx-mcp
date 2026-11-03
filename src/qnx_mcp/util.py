# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Errors, configuration, bounded subprocess execution, locking and input validation."""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import fnmatch
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        home = Path(env.get("HOME", str(Path.home())))
        base = Path(env.get("QNX_MCP_HOME", str(home / ".qnx-mcp")))
        allow = tuple(p.strip() for p in env.get("QNX_MCP_ALLOW_TARGETS", "*").split(",") if p.strip())
        return cls(
            sdp_env=Path(env.get("QNX_MCP_SDP_ENV", str(home / "qnx800" / "qnxsdp-env.sh"))),
            targets_dir=Path(env.get("QNX_MCP_TARGETS_DIR", str(base / "targets"))),
            state_dir=Path(env.get("QNX_MCP_STATE_DIR", str(base / "state"))),
            allow=allow or ("*",),
            ssh_key=Path(env.get("QNX_MCP_SSH_KEY", str(home / ".ssh" / "id_ed25519"))),
            leases=Path(env.get("QNX_MCP_DHCP_LEASES", "/var/lib/libvirt/dnsmasq/virbr0.status")),
        )

    def allowed(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.allow)


# ---------------------------------------------------------------- validation

TARGET_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# Remote paths are passed to scp, whose legacy protocol lets the remote shell see them,
# so they are restricted to characters that need no quoting at all.
REMOTE_PATH_RE = re.compile(r"^/[A-Za-z0-9._+/-]{1,255}$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def check_target_name(cfg: Config, name: str) -> str:
    if not isinstance(name, str) or not TARGET_NAME_RE.match(name):
        raise QnxMcpError(
            "invalid_argument",
            f"invalid target name {name!r}",
            hint="Use 1-32 characters: a lowercase letter followed by lowercase letters, digits, '-' or '_'.",
        )
    if not cfg.allowed(name):
        raise QnxMcpError(
            "target_not_allowed",
            f"target {name!r} is outside this server's allowlist {list(cfg.allow)}",
            hint="Use one of the allowed target names (QNX_MCP_ALLOW_TARGETS); other targets belong to someone else.",
        )
    return name


def check_remote_path(path: str, what: str = "remote path") -> str:
    if not REMOTE_PATH_RE.match(path or "") or "/../" in f"{path}/" or path.endswith("/.."):
        raise QnxMcpError(
            "invalid_argument",
            f"unsafe {what} {path!r}",
            hint="Use an absolute path made of letters, digits and . _ + - / only, without '..'.",
        )
    return path


def quote_argv(argv: Sequence[str]) -> str:
    """Quote an argument vector for the remote POSIX shell (QNX sh): one word per element."""
    return " ".join(shlex.quote(str(a)) for a in argv)


# ---------------------------------------------------------------- processes


@dataclasses.dataclass
class ProcResult:
    argv: list[str]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_s: float

    @property
    def out(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace")


def _kill_group(p: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    input: bytes | None = None,
    log_path: Path | None = None,
) -> ProcResult:
    """Run argv (never through a shell) with a hard timeout.

    The child gets its own session so that on timeout the whole process group
    (ssh, make, compilers...) is killed. With log_path, stdout and stderr go to
    that file instead of pipes; use it for commands that leave background
    processes behind (mkqnximage --run starts QEMU).
    """
    argv = [str(a) for a in argv]
    start = time.monotonic()
    log_f = open(log_path, "ab") if log_path else None
    try:
        try:
            p = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=log_f if log_f else subprocess.PIPE,
                stderr=subprocess.STDOUT if log_f else subprocess.PIPE,
                env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd else None,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise QnxMcpError("tool_missing", f"executable not found: {argv[0]}", hint="Check PATH and the QNX SDP installation.") from e
        timed_out = False
        try:
            out, err = p.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p)
            try:
                out, err = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
        return ProcResult(argv, None if timed_out else p.returncode, out or b"", err or b"", timed_out, time.monotonic() - start)
    finally:
        if log_f:
            log_f.close()


def tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


# ---------------------------------------------------------------- locking


@contextlib.contextmanager
def file_lock(path: Path, *, exclusive: bool, timeout: float, what: str) -> Iterator[None]:
    """flock-based lock shared by every qnx-mcp process on this host."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
