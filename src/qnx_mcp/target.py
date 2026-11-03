# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""QEMU targets built with mkqnximage, one directory per target, plus SSH access."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import select
import shlex
import shutil
import signal
import socket
import time
from pathlib import Path
from typing import Any, Sequence

from .sdk import ARCHES, check_arch, sdk_env, sdk_tool
from .util import (
    Config, ProcResult, QnxMcpError, check_cancelled, check_remote_path, check_target_name, file_lock, private_dir,
    quote_argv, run, tail, wait_until, write_private,
)

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
_RAM_RE = re.compile(r"\d{1,6}[MG]")

# Restarts the image's dumper with the same arguments as mkqnximage's startup script plus
# -n (sequential core names: app.1.core, app.2.core, ...), so two processes with the same
# name never overwrite each other's core. Only written for images without security policies.
SEQUENTIAL_DUMPER_SNIPPET = """\
# local/snippets/post_start.custom (written by qnx-mcp)
# Commands executed from post_startup.sh at the end of system startup.
# qnx-mcp: restart dumper with sequential core file names (dumper -n) so that cores of
# two processes with the same name never overwrite each other.
slay dumper
dumper -U 30:30 -d /data/var/dumper -M 1000 -D cores -n
"""


def validate_mkqnximage_options(options: Sequence[str]) -> list[str]:
    out = []
    for opt in options:
        key, sep, value = str(opt).partition("=")
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
        m = re.fullmatch(r"(OPT_[A-Z0-9_]+)=(.*)", line.strip())
        if m:
            with contextlib.suppress(ValueError):
                parts = shlex.split(m.group(2))
                opts[m.group(1)] = parts[0] if parts else ""
    return opts


def parse_leases(text: str, mac: str, now: float) -> list[str]:
    """Live IPv4 leases for mac, newest first. dnsmasq writes expiry-time 0 for infinite leases."""
    try:
        leases = json.loads(text)
    except ValueError:
        return []
    out = []
    for lease in leases if isinstance(leases, list) else []:
        if not isinstance(lease, dict) or str(lease.get("mac-address", "")).lower() != mac.lower():
            continue
        try:
            expiry = int(lease.get("expiry-time", 0))
            ip = str(ipaddress.IPv4Address(str(lease.get("ip-address", ""))))
        except ValueError:
            continue
        if expiry != 0 and expiry <= now:
            continue
        out.append((expiry if expiry else float("inf"), ip))
    return [ip for _, ip in sorted(out, reverse=True)]


class Target:
    def __init__(self, cfg: Config, name: str, *, must_exist: bool = True):
        self.cfg = cfg
        self.name = check_target_name(cfg, name)
        self.dir = cfg.targets_dir / name
        self.state = cfg.state_dir / "targets" / name
        self.known_hosts = self.state / "known_hosts"
        self.alias = f"qnx-mcp-{name}"
        if os.path.lexists(self.dir):
            # A symlink (or a directory reached through one) could point at someone else's target.
            if self.dir.is_symlink() or os.path.realpath(self.dir) != os.path.join(os.path.realpath(cfg.targets_dir), name):
                raise QnxMcpError(
                    "target_not_allowed",
                    f"{self.dir} is a symlink or resolves outside {cfg.targets_dir}",
                    hint="qnx-mcp only manages real directories directly inside the targets dir.",
                )
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
        """Exclusive for lifecycle changes; shared for everything that only uses a running target."""
        private_dir(self.state)
        return file_lock(self.state / "lock", exclusive=exclusive, timeout=timeout, what=f"target {self.name}")

    def records_lock(self) -> contextlib.AbstractContextManager[None]:
        private_dir(self.state)
        return file_lock(self.state / "records.lock", exclusive=True, timeout=30, what=f"run records of {self.name}")

    # ------------------------------------------------------------ QEMU process identity

    def _verified(self, pid: int) -> bool:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return False
        return "qemu-system" in cmdline and os.path.realpath(cwd) == os.path.realpath(self.dir)

    def qemu_pidfd(self) -> tuple[int, int] | None:
        """(pid, pidfd) of this target's QEMU. The pidfd is opened first and the process verified
        afterwards, so later signals reach exactly the process that was verified."""
        try:
            pid = int((self.dir / "output" / "qemu.pid").read_text().strip())
            fd = os.pidfd_open(pid)
        except (OSError, ValueError):
            return None
        try:
            if self._verified(pid):
                signal.pidfd_send_signal(fd, 0)  # still the same process: it cannot have exited and been replaced
                return pid, fd
        except OSError:
            pass
        os.close(fd)
        return None

    def qemu_pid(self) -> int | None:
        got = self.qemu_pidfd()
        if got is None:
            return None
        os.close(got[1])
        return got[0]

    # ------------------------------------------------------------ address

    def lookup_ip(self, fallback: bool = True, getip_timeout: float = 25) -> str | None:
        """Current IPv4 address: newest live DHCP lease for this target's MAC whose port 22 answers,
        else mkqnximage --getip (arp). Identity is not taken from the address: the pinned host key is
        what proves which machine answered."""
        mac = self.mac or ""
        if not mac:
            return None
        try:
            text = self.cfg.leases.read_text()
        except OSError:
            text = "[]"
        for ip in parse_leases(text, mac, time.time()):
            if _tcp_open(ip, 22, 1.0):
                return ip
        if not fallback:
            return None
        r = run([sdk_tool(sdk_env(self.cfg), "mkqnximage"), "--getip"], timeout=getip_timeout, env=sdk_env(self.cfg), cwd=self.dir)
        for line in reversed(r.out.splitlines()):
            with contextlib.suppress(ValueError):
                return str(ipaddress.IPv4Address(line.strip()))
        return None

    # ------------------------------------------------------------ host keys and ssh

    def image_host_keys(self) -> list[str]:
        """Host public keys that mkqnximage generated on this host (--sshd-pregen=yes, its default)."""
        keys = []
        for kind in ("ed25519", "rsa"):
            with contextlib.suppress(OSError):
                fields = (self.dir / "local" / "misc_files" / f"ssh_host_{kind}_key.pub").read_text().split()
                if len(fields) >= 2 and re.fullmatch(r"ssh-[a-z0-9-]+", fields[0]) and re.fullmatch(r"[A-Za-z0-9+/=]+", fields[1]):
                    keys.append(f"{fields[0]} {fields[1]}")
        return keys

    def host_key_policy(self) -> str:
        return "image" if self.image_host_keys() else "first_use"

    def pin_host_keys(self) -> str:
        """Pin the image's own host keys under this target's HostKeyAlias. Images without
        pre-generated keys are pinned on first contact and kept until target_create/destroy."""
        keys = self.image_host_keys()
        if keys:
            write_private(self.known_hosts, "".join(f"{self.alias} {k}\n" for k in keys).encode())
            return "image"
        return "first_use"

    def _ssh_opts(self) -> list[str]:
        private_dir(self.state)
        keys = self.image_host_keys()
        try:
            have = self.known_hosts.read_text()
        except OSError:
            have = ""
        if keys:
            # The image's key files on this host are the source of truth; re-pin if the file drifted.
            want = "".join(f"{self.alias} {k}\n" for k in keys)
            if have != want:
                write_private(self.known_hosts, want.encode())
            strict = "yes"
        else:
            strict = "yes" if any(line.startswith(self.alias + " ") for line in have.splitlines()) else "accept-new"
        return [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", f"HostKeyAlias={self.alias}",
            "-o", "CheckHostIP=no",
            "-o", "UpdateHostKeys=no",
            "-o", f"StrictHostKeyChecking={strict}",
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

    def _classify(self, r: ProcResult, what: str, timeout: float, check: bool) -> None:
        if r.timed_out:
            raise QnxMcpError("timeout", f"{what} on {self.name} timed out after {timeout:.0f}s", hint="Increase timeout_s or check that the target is responsive (target_status).")
        if r.returncode == 255:
            err = r.err
            if "REMOTE HOST IDENTIFICATION HAS CHANGED" in err or "Host key verification failed" in err or "host key for" in err.lower():
                raise QnxMcpError(
                    "host_key_mismatch",
                    f"the machine at {self.name}'s address did not present {self.name}'s pinned SSH host key",
                    hint="Another VM may hold this address (stale lease). If you rebuilt the image outside qnx-mcp with new keys, delete the target's known_hosts in the qnx-mcp state dir.",
                    stderr=tail(err, 800),
                )
            raise QnxMcpError("ssh_failed", f"ssh to {self.name} failed during {what}", hint="Check target_status; the target may be down or booting.", stderr=tail(err, 800))
        if check and r.returncode != 0:
            raise QnxMcpError(
                "remote_failed",
                f"{what} on {self.name} exited with status {r.returncode}",
                hint="See details.stderr; the target may be missing a utility or a path.",
                stdout=tail(r.out, 800),
                stderr=tail(r.err, 800),
            )

    def ssh(self, argv: Sequence[str], *, timeout: float, what: str = "remote command", ip: str | None = None,
            check: bool = True, hold_stdin: bool = False, max_output: int = 8 << 20) -> ProcResult:
        """Run argv on the target; every element is quoted for the remote shell."""
        ip = ip or self.require_ip()
        r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", quote_argv(argv)], timeout=timeout, hold_stdin=hold_stdin, max_output=max_output)
        self._classify(r, what, timeout, check)
        return r

    def ssh_script(self, script: str, *, timeout: float, what: str, ip: str | None = None, check: bool = True) -> ProcResult:
        """Run a fixed shell script. Callers build `script` only from constants and quote_argv()."""
        ip = ip or self.require_ip()
        r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", script], timeout=timeout)
        self._classify(r, what, timeout, check)
        return r

    def put(self, local: Path, remote: str, *, timeout: float, ip: str | None = None) -> None:
        check_remote_path(remote)
        ip = ip or self.require_ip()
        r = run(["scp", "-q", "-p", *self._ssh_opts(), str(local), f"root@{ip}:{remote}"], timeout=timeout)
        self._classify(r, f"copy to {remote}", timeout, check=False)
        if r.returncode != 0:
            raise QnxMcpError("deploy_failed", f"scp to {self.name}:{remote} failed", stderr=tail(r.err, 800))

    def get(self, remote: str, local: Path, *, timeout: float, ip: str | None = None) -> None:
        """Copy one remote file to a new local path (the caller verifies size and hash)."""
        check_remote_path(remote)
        ip = ip or self.require_ip()
        private_dir(local.parent)
        r = run(["scp", "-q", *self._ssh_opts(), f"root@{ip}:{remote}", str(local)], timeout=timeout)
        self._classify(r, f"copy from {remote}", timeout, check=False)
        if r.returncode != 0:
            raise QnxMcpError("copy_failed", f"scp from {self.name}:{remote} failed", stderr=tail(r.err, 800))

    # ------------------------------------------------------------ lifecycle

    def wait_ssh(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        last = "no IP yet"
        next_getip = time.monotonic() + 8  # the lease file usually wins; arp fallback every 10 s after that
        while time.monotonic() < deadline:
            check_cancelled()
            if self.qemu_pid() is None:
                raise QnxMcpError("target_down", f"QEMU for {self.name} exited during boot", hint=f"See {self.log_path}.", log=self.log_tail())
            use_getip = time.monotonic() >= next_getip
            if use_getip:
                next_getip = time.monotonic() + 10
            ip = self.lookup_ip(fallback=use_getip, getip_timeout=8)
            if ip:
                r = run(["ssh", *self._ssh_opts(), f"root@{ip}", "--", "true"], timeout=10)
                if r.returncode == 0:
                    return ip
                if "Host key verification failed" in r.err or "REMOTE HOST IDENTIFICATION" in r.err:
                    last = f"{ip} answered with a different host key"
                else:
                    last = tail(r.err, 300) or f"ssh exit {r.returncode}"
            time.sleep(2)
        raise QnxMcpError("timeout", f"target {self.name} did not accept SSH within {timeout:.0f}s", hint="Call target_status; boot may be slow or the image lacks sshd.", last_error=last)

    @property
    def log_path(self) -> Path:
        return self.state / "mkqnximage.log"

    def log_tail(self) -> str:
        try:
            return tail(self.log_path.read_text(errors="replace"), 2000)
        except OSError:
            return ""

    def _mkqnximage(self, args: list[str], timeout: float) -> ProcResult:
        env = sdk_env(self.cfg)
        private_dir(self.state)
        log = self.log_path  # outside the target dir: mkqnximage refuses to build into a non-empty directory
        with open(log, "a") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} mkqnximage {' '.join(args)}\n")
        os.chmod(log, 0o600)
        r = run([sdk_tool(env, "mkqnximage"), *args], timeout=timeout, env=env, cwd=self.dir, log_path=log)
        if r.timed_out:
            raise QnxMcpError("timeout", f"mkqnximage {' '.join(args)} timed out after {timeout:.0f}s", log=self.log_tail())
        if r.returncode != 0:
            raise QnxMcpError("mkqnximage_failed", f"mkqnximage exited with {r.returncode}", hint="Read details.log; fix the option or the SDP installation.", log=self.log_tail())
        return r

    def create(self, *, arch: str, ram: str, cpus: int, options: list[str], boot_timeout: float) -> dict[str, Any]:
        check_arch(arch)
        if not isinstance(ram, str) or not _RAM_RE.fullmatch(ram):
            raise QnxMcpError("invalid_argument", f"bad ram {ram!r}", hint="Use e.g. 1G or 2048M.")
        if not 1 <= cpus <= 4:
            raise QnxMcpError("invalid_argument", "cpus must be 1-4 (mkqnximage --cpu)")
        opts = validate_mkqnximage_options(options)
        pub = Path(str(self.cfg.ssh_key) + ".pub")
        if not pub.is_file():
            raise QnxMcpError("ssh_key_missing", f"public key {pub} not found", hint="Create one with ssh-keygen -t ed25519 or set QNX_MCP_SSH_KEY.")
        secpol = next((o.partition("=")[2] for o in opts if o.startswith("--secpol=")), "no")
        with self.lock(exclusive=True):
            if os.path.lexists(self.dir) and (self.dir.is_symlink() or not self.dir.is_dir() or any(self.dir.iterdir())):
                raise QnxMcpError("target_exists", f"target directory {self.dir} already exists and is not empty", hint="Use target_start, or pick a new name.")
            created = not os.path.lexists(self.dir)
            self.dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                self.known_hosts.unlink()
            try:
                if secpol == "no":
                    # A pre-existing local/snippets also tells mkqnximage this is a deliberate new image dir.
                    (self.dir / "local" / "snippets").mkdir(parents=True)
                    (self.dir / "local" / "snippets" / "post_start.custom").write_text(SEQUENTIAL_DUMPER_SNIPPET)
                self._mkqnximage(
                    ["--type=qemu", f"--arch={arch}", f"--ssh-ident={pub}", "--sshd-pregen=yes", "--noprompt",
                     f"--ram={ram}", f"--cpu={cpus}", *opts, "--run=-h"],
                    timeout=1200,
                )
                self.pin_host_keys()
                self.wait_ssh(boot_timeout)
            except BaseException as e:
                cleanup = self._remove(created)
                if isinstance(e, QnxMcpError):
                    e.details["cleanup"] = cleanup
                raise
        return self.status(probe=True)

    def _stop_qemu(self, timeout: float = 30) -> bool:
        got = self.qemu_pidfd()
        if got is None:
            return False
        pid, fd = got
        try:
            with contextlib.suppress(QnxMcpError):
                run([sdk_tool(sdk_env(self.cfg), "mkqnximage"), "--stop"], timeout=timeout, env=sdk_env(self.cfg), cwd=self.dir)
            for sig, wait in ((None, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                if sig is not None:
                    with contextlib.suppress(ProcessLookupError):
                        signal.pidfd_send_signal(fd, sig)
                if select.select([fd], [], [], wait)[0]:  # a pidfd becomes readable when the process exits
                    break
        finally:
            os.close(fd)
        return True

    def _remove(self, created_dir: bool) -> str:
        """Failed create or destroy: stop QEMU, delete the image directory and the server state (not the lock)."""
        stopped = self._stop_qemu()
        if created_dir or (self.dir / "local" / "options").is_file() or (self.dir / "local" / "snippets").is_dir():
            shutil.rmtree(self.dir, ignore_errors=True)  # fd-based on Linux: does not follow symlinks inside
        for p in self.state.iterdir() if self.state.is_dir() else []:
            if p.name in ("lock", "records.lock", "mkqnximage.log"):
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    p.unlink()
        return f"{'stopped QEMU, ' if stopped else ''}removed {self.dir}"

    def destroy(self) -> dict[str, Any]:
        with self.lock(exclusive=True):
            was_running = self.qemu_pid() is not None
            done = self._remove(created_dir=False)
        return {"name": self.name, "destroyed": True, "was_running": was_running, "detail": done}

    def start(self, boot_timeout: float) -> dict[str, Any]:
        with self.lock(exclusive=True):
            if self.qemu_pid() is None:
                self._mkqnximage(["--noprompt", "--run=-h"], timeout=600)
            self.pin_host_keys()
            self.wait_ssh(boot_timeout)
        return self.status(probe=True)

    def stop(self, timeout: float = 30) -> dict[str, Any]:
        with self.lock(exclusive=True):
            was_running = self._stop_qemu(timeout)
            if was_running and not wait_until(lambda: self.qemu_pid() is None, 5):
                raise QnxMcpError("internal_error", f"QEMU for {self.name} survived SIGKILL")
        st = self.status(probe=False)
        st["was_running"] = was_running
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
            "host_key_policy": self.host_key_policy(),
            "ip": None,
        }
        if pid is None or not probe:
            return st
        with self.lock(exclusive=False):
            ip = self.lookup_ip()
            st["ip"] = ip
            if not ip:
                st["state"] = "booting"
                return st
            script = ("uname -a && date +%s && if test -e /proc/dumper; then echo dumper=yes; pidin -p dumper -F %A | sed -n 2p; "
                      "else echo dumper=no; fi")
            try:
                r = self.ssh_script(script, timeout=20, what="status probe", ip=ip)
                lines = r.out.splitlines()
                if len(lines) < 3 or not lines[1].strip().isdigit() or lines[2] not in ("dumper=yes", "dumper=no"):
                    raise QnxMcpError("remote_failed", "status probe output was not recognised", stdout=tail(r.out, 400))
                st["ssh"] = "ok"
                st["uname"] = lines[0]
                st["target_time"] = int(lines[1])
                st["dumper_running"] = lines[2] == "dumper=yes"
                dumper = lines[3].strip() if len(lines) > 3 else ""
                st["dumper_args"] = dumper or None
                st["core_naming"] = "sequential" if re.search(r"\s-[nN]\b", f" {dumper}") else "fixed"
            except QnxMcpError as e:
                st["state"] = "booting" if e.code in ("ssh_failed", "timeout") else st["state"]
                st["ssh"] = e.to_dict()
        return st


def list_targets(cfg: Config) -> list[dict[str, Any]]:
    out = []
    if not cfg.targets_dir.is_dir():
        return out
    for d in sorted(cfg.targets_dir.iterdir()):
        if d.is_symlink() or not (d / "local" / "options").is_file() or not cfg.allowed(d.name):
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
