# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""QEMU targets built with mkqnximage, one directory per target, plus SSH access."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import signal
import socket
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from .sdk import ARCHES, check_arch, sdk_env, sdk_tool
from .util import Config, ProcResult, QnxMcpError, check_remote_path, check_target_name, file_lock, quote_argv, run, tail

# mkqnximage options an agent may pass through target_create, with value patterns.
# Values end up inside mkqnximage's own sourced option files, so they are kept to
# characters that cannot break out of its quoting.
MKQNXIMAGE_OPTIONS = {
    "--hostname": r"[A-Za-z0-9-]{1,63}",
    "--data-size": r"\d{1,6}",
    "--sys-size": r"\d{1,6}",
    "--boot-size": r"\d{1,6}",
    "--data-inodes": r"\d{1,8}",
    "--sys-inodes": r"\d{1,8}",
    "--aslr": r"yes|no",
    "--secure-procfs": r"yes|no",
    "--python": r"yes|no",
    "--perl": r"yes|no",
    "--valgrind": r"yes|no",
    "--secpol": r"no|develop|open|secure",
    "--tz": r"[A-Za-z0-9_+/-]{1,64}",
    "--time-servers": r"[A-Za-z0-9.:-]{1,200}",
}
_RAM_RE = re.compile(r"^\d{2,6}[MG]$")


def validate_mkqnximage_options(options: Sequence[str]) -> list[str]:
    out = []
    for opt in options:
        key, sep, value = opt.partition("=")
        pattern = MKQNXIMAGE_OPTIONS.get(key)
        if pattern is None or not sep or not re.fullmatch(pattern, value):
            raise QnxMcpError(
                "invalid_argument",
                f"mkqnximage option not allowed: {opt!r}",
                hint=f"Allowed: {', '.join(f'{k}=<{v}>' for k, v in MKQNXIMAGE_OPTIONS.items())}",
            )
        out.append(opt)
    return out


def _read_options(path: Path) -> dict[str, str]:
    opts: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return opts
    for line in text.splitlines():
        m = re.match(r"^(OPT_[A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            with contextlib.suppress(ValueError):
                parts = shlex.split(m.group(2))
                opts[m.group(1)] = parts[0] if parts else ""
    return opts


class Target:
    def __init__(self, cfg: Config, name: str, *, must_exist: bool = True):
        self.cfg = cfg
        self.name = check_target_name(cfg, name)
        self.dir = cfg.targets_dir / name
        self.state = cfg.state_dir / "targets" / name
        self.known_hosts = self.state / "known_hosts"
        if must_exist and not (self.dir / "local" / "options").is_file():
            raise QnxMcpError(
                "target_not_found",
                f"no target named {name!r} in {cfg.targets_dir}",
                hint="Call target_list to see targets, or target_create to build one.",
            )

    # ------------------------------------------------------------ metadata

    @property
    def options(self) -> dict[str, str]:
        return _read_options(self.dir / "local" / "options")

    @property
    def arch(self) -> str:
        return self.options.get("OPT_ARCH", "x86_64")

    @property
    def mac(self) -> str | None:
        return self.options.get("OPT_MACADDR")

    def lock(self, exclusive: bool, timeout: float = 120) -> contextlib.AbstractContextManager[None]:
        return file_lock(self.state / "lock", exclusive=exclusive, timeout=timeout, what=f"target {self.name}")

    def qemu_pid(self) -> int | None:
        """PID of this target's QEMU, verified via /proc so a recycled PID is never trusted."""
        try:
            pid = int((self.dir / "output" / "qemu.pid").read_text().strip())
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except (OSError, ValueError):
            return None
        if "qemu-system" not in cmdline or os.path.realpath(cwd) != os.path.realpath(self.dir):
            return None
        return pid

    def lookup_ip(self, fallback: bool = True) -> str | None:
        """Current IPv4 address: newest DHCP lease for this target's MAC, else mkqnximage --getip."""
        mac = (self.mac or "").lower()
        if not mac:
            return None
        try:
            leases = json.loads(self.cfg.leases.read_text())
        except (OSError, ValueError):
            leases = []
        mine = sorted((l for l in leases if str(l.get("mac-address", "")).lower() == mac), key=lambda l: l.get("expiry-time", 0), reverse=True)
        if mine and _tcp_open(mine[0]["ip-address"], 22, 1.0):
            return mine[0]["ip-address"]
        if not fallback:
            return None
        r = run(["mkqnximage", "--getip"], timeout=25, env=sdk_env(self.cfg), cwd=self.dir)
        lines = [l.strip() for l in r.out.splitlines() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", l.strip())]
        return lines[-1] if lines else None

    # ------------------------------------------------------------ ssh

    def _ssh_opts(self) -> list[str]:
        self.state.mkdir(parents=True, exist_ok=True)
        return [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR",
            "-o", "IdentitiesOnly=yes",
            "-i", str(self.cfg.ssh_key),
        ]

    def require_ip(self) -> str:
        if self.qemu_pid() is None:
            raise QnxMcpError("target_down", f"target {self.name} is not running", hint=f"Call target_start with name={self.name!r}.")
        ip = self.lookup_ip()
        if not ip:
            raise QnxMcpError(
                "target_down",
                f"target {self.name} is running but has no reachable IP yet",
                hint="It may still be booting; call target_status again in a few seconds.",
            )
        return ip

    def _classify(self, r: ProcResult, what: str, timeout: float) -> None:
        if r.timed_out:
            raise QnxMcpError("timeout", f"{what} on {self.name} timed out after {timeout:.0f}s", hint="Increase timeout_s or check that the target is responsive (target_status).")
        if r.returncode == 255:
            err = r.err
            if "REMOTE HOST IDENTIFICATION HAS CHANGED" in err or "Host key verification failed" in err:
                raise QnxMcpError(
                    "host_key_mismatch",
                    f"the host at {self.name}'s address presented a different SSH host key",
                    hint="If the target was rebuilt, call target_start (it re-pins the key); otherwise another machine took its IP.",
                    stderr=tail(err, 800),
                )
            raise QnxMcpError("ssh_failed", f"ssh to {self.name} failed during {what}", hint="Check target_status; the target may be down or booting.", stderr=tail(err, 800))

    def ssh(self, argv: Sequence[str], *, timeout: float, what: str = "remote command", ip: str | None = None) -> ProcResult:
        """Run argv on the target; every element is quoted for the remote shell."""
        ip = ip or self.require_ip()
        r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", quote_argv(argv)], timeout=timeout)
        self._classify(r, what, timeout)
        return r

    def ssh_script(self, script: str, *, timeout: float, what: str, ip: str | None = None) -> ProcResult:
        """Run a fixed shell script. Callers build `script` only from constants and quote_argv()."""
        ip = ip or self.require_ip()
        r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", script], timeout=timeout)
        self._classify(r, what, timeout)
        return r

    def put(self, local: Path, remote: str, *, timeout: float, ip: str | None = None) -> None:
        check_remote_path(remote)
        ip = ip or self.require_ip()
        r = run(["scp", "-q", "-p", *self._ssh_opts(), str(local), f"root@{ip}:{remote}"], timeout=timeout)
        self._classify(r, f"copy to {remote}", timeout)
        if r.returncode != 0:
            raise QnxMcpError("deploy_failed", f"scp to {self.name}:{remote} failed", stderr=tail(r.err, 800))

    def get(self, remote: str, local: Path, *, timeout: float, ip: str | None = None) -> None:
        check_remote_path(remote)
        ip = ip or self.require_ip()
        local.parent.mkdir(parents=True, exist_ok=True)
        r = run(["scp", "-q", *self._ssh_opts(), f"root@{ip}:{remote}", str(local)], timeout=timeout)
        self._classify(r, f"copy from {remote}", timeout)
        if r.returncode != 0:
            raise QnxMcpError("copy_failed", f"scp from {self.name}:{remote} failed", stderr=tail(r.err, 800))

    # ------------------------------------------------------------ lifecycle

    def reset_host_key(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.known_hosts.unlink()

    def wait_ssh(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        last = "no IP yet"
        while time.monotonic() < deadline:
            if self.qemu_pid() is None:
                raise QnxMcpError("target_down", f"QEMU for {self.name} exited during boot", hint="See the log in the target directory (qnx-mcp.log).", log=self.log_tail())
            ip = self.lookup_ip(fallback=False)
            if ip:
                r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", "true"], timeout=10)
                if r.returncode == 0:
                    return ip
                last = tail(r.err, 300) or f"ssh exit {r.returncode}"
            time.sleep(2)
        raise QnxMcpError("timeout", f"target {self.name} did not accept SSH within {timeout:.0f}s", hint="Call target_status; boot may be slow or the image lacks sshd.", last_error=last)

    def log_tail(self) -> str:
        try:
            return tail((self.dir / "qnx-mcp.log").read_text(errors="replace"), 2000)
        except OSError:
            return ""

    def _mkqnximage(self, args: list[str], timeout: float) -> ProcResult:
        env = sdk_env(self.cfg)
        log = self.dir / "qnx-mcp.log"
        with open(log, "a") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} mkqnximage {' '.join(args)}\n")
        r = run([sdk_tool(env, "mkqnximage"), *args], timeout=timeout, env=env, cwd=self.dir, log_path=log)
        if r.timed_out:
            raise QnxMcpError("timeout", f"mkqnximage {' '.join(args)} timed out after {timeout:.0f}s", log=self.log_tail())
        if r.returncode != 0:
            raise QnxMcpError("mkqnximage_failed", f"mkqnximage exited with {r.returncode}", hint="Read details.log; fix the option or the SDP installation.", log=self.log_tail())
        return r

    def create(self, *, arch: str, ram: str, cpus: int, options: list[str], boot_timeout: float) -> dict[str, Any]:
        check_arch(arch)
        if not _RAM_RE.match(ram):
            raise QnxMcpError("invalid_argument", f"bad ram {ram!r}", hint="Use e.g. 1G or 2048M.")
        if not 1 <= cpus <= 4:
            raise QnxMcpError("invalid_argument", "cpus must be 1-4 (mkqnximage --cpu)")
        opts = validate_mkqnximage_options(options)
        pub = Path(str(self.cfg.ssh_key) + ".pub")
        if not pub.is_file():
            raise QnxMcpError("ssh_key_missing", f"public key {pub} not found", hint="Create one with ssh-keygen -t ed25519 or set QNX_MCP_SSH_KEY.")
        with self.lock(exclusive=True):
            if self.dir.exists() and any(self.dir.iterdir()):
                raise QnxMcpError("target_exists", f"target directory {self.dir} already exists and is not empty", hint="Use target_start, or pick a new name.")
            self.dir.mkdir(parents=True, exist_ok=True)
            self.reset_host_key()
            self._mkqnximage(
                ["--type=qemu", f"--arch={arch}", f"--ssh-ident={pub}", "--noprompt", f"--ram={ram}", f"--cpu={cpus}", *opts, "--run=-h"],
                timeout=1200,
            )
            self.wait_ssh(boot_timeout)
        return self.status(probe=True)

    def start(self, boot_timeout: float) -> dict[str, Any]:
        with self.lock(exclusive=True):
            if self.qemu_pid() is None:
                self.reset_host_key()
                self._mkqnximage(["--noprompt", "--run=-h"], timeout=600)
            self.wait_ssh(boot_timeout)
        return self.status(probe=True)

    def stop(self, timeout: float = 30) -> dict[str, Any]:
        with self.lock(exclusive=True):
            pid = self.qemu_pid()
            if pid is not None:
                run(["mkqnximage", "--stop"], timeout=timeout, env=sdk_env(self.cfg), cwd=self.dir)
                deadline = time.monotonic() + 10
                while self.qemu_pid() is not None and time.monotonic() < deadline:
                    time.sleep(0.3)
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    if self.qemu_pid() is None:
                        break
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, sig)
                    time.sleep(1)
        st = self.status(probe=False)
        st["was_running"] = pid is not None
        return st

    def status(self, probe: bool = True) -> dict[str, Any]:
        opts = self.options
        pid = self.qemu_pid()
        st: dict[str, Any] = {
            "name": self.name,
            "state": "stopped" if pid is None else "running",
            "qemu_pid": pid,
            "arch": opts.get("OPT_ARCH", "x86_64"),
            "ram": opts.get("OPT_RAM"),
            "cpus": int(opts["OPT_CPU"]) if opts.get("OPT_CPU", "").isdigit() else None,
            "mac": opts.get("OPT_MACADDR"),
            "directory": str(self.dir),
            "ip": None,
        }
        if pid is None or not probe:
            return st
        ip = self.lookup_ip()
        st["ip"] = ip
        if not ip:
            st["state"] = "booting"
            return st
        script = "uname -a; date +%s; if test -e /proc/dumper; then echo dumper=yes; else echo dumper=no; fi"
        try:
            r = self.ssh_script(script, timeout=20, what="status probe", ip=ip)
            lines = r.out.splitlines()
            st["ssh"] = "ok"
            st["uname"] = lines[0] if lines else None
            st["target_time"] = int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else None
            st["dumper_running"] = any(l == "dumper=yes" for l in lines)
        except QnxMcpError as e:
            st["state"] = "booting" if e.code in ("ssh_failed", "timeout") else st["state"]
            st["ssh"] = e.to_dict()
        return st


def list_targets(cfg: Config) -> list[dict[str, Any]]:
    out = []
    if not cfg.targets_dir.is_dir():
        return out
    for d in sorted(cfg.targets_dir.iterdir()):
        if not (d / "local" / "options").is_file() or not cfg.allowed(d.name):
            continue
        try:
            out.append(Target(cfg, d.name).status(probe=False))
        except QnxMcpError:
            continue
    return out


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    with contextlib.suppress(OSError):
        with socket.create_connection((host, port), timeout=timeout):
            return True
    return False


def gdb_for(arch: str) -> str:
    return f"{ARCHES[arch][1]}-gdb"
