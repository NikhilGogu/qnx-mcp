# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Targets, one directory each: QEMU guests built with mkqnximage, and existing QNX machines reachable only over SSH."""

from __future__ import annotations

import contextlib
import dataclasses
import ipaddress
import json
import os
import re
import select
import shlex
import shutil
import signal
import stat
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .abilities import LOGIN_USER, privileges
from .sdk import ARCHES, check_arch, sdk_env, sdk_tool
from .util import (
    Config, ProcResult, QnxMcpError, check_cancelled, check_remote_path, check_target_name, file_lock, private_dir,
    quote_argv, run, tail, wait_until, write_private,
)

# ---- SSH-only targets (kind "ssh"): a machine qnx-mcp did not build and cannot power on or off.
# The registration is one file in the target's directory; the directory has no local/options.
SSH_TARGET_FILE = "ssh-target.json"
KIND_QEMU, KIND_SSH = "qemu", "ssh"
SSH_DEFAULT_USER = "qnxuser"  # the login of QNX's official QNX OS 8.0 AMI
_SSH_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_HOSTNAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?){0,10}")
_NUMERIC_LABEL_RE = re.compile(r"0[xX][0-9A-Fa-f]*")
# ssh-keygen -l output for SHA-256: 32 bytes in unpadded base64
FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

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
# ssh reads UserKnownHostsFile as a whitespace-separated list and expands %-tokens and ${VAR} in each name
# (ssh_config(5)), so a state path with any of these would silently point the host-key pin elsewhere.
_SSH_UNSAFE_PATH_RE = re.compile(r"[\s\"'\\%$]")
STATE_DIR_FIX = "Set QNX_MCP_STATE_DIR to a path without whitespace, quotes, backslashes, '%' or '$'."
# ssh also expands %-tokens and ${VAR} in the -i identity file name (ssh_config(5), IdentityFile).
_SSH_KEY_UNSAFE_RE = re.compile(r"[%$]")
SSH_KEY_FIX = "Set QNX_MCP_SSH_KEY to a path without '%' or '$'."


def ssh_path_problem(path: Path) -> str | None:
    """Why ssh would not read this path as one file name in an -o option (None: it would)."""
    m = _SSH_UNSAFE_PATH_RE.search(str(path))
    return f"{path} contains {m.group()!r}, which ssh treats as a separator or expands in UserKnownHostsFile" if m else None


def ssh_key_problem(path: Path) -> str | None:
    """Why ssh would not open this exact file for -i (None: it would)."""
    m = _SSH_KEY_UNSAFE_RE.search(str(path))
    return f"{path} contains {m.group()!r}, which ssh expands in an identity file name" if m else None


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


def check_ssh_host(host: Any) -> str:
    """A host name or IP literal that ssh will read as exactly that machine. Link-local, unspecified, multicast
    and reserved addresses are refused (the cloud metadata services live there), and so are names that the
    resolver would read as a number ('2852039166', '0xa9fea9fe', '1.2.3'), which could name the same places."""
    bad = QnxMcpError("invalid_argument", f"invalid ssh host {host!r}",
                      hint="Use a DNS name or an IPv4/IPv6 address (no scheme, port, user or zone id).")
    if not isinstance(host, str) or not 1 <= len(host) <= 253:
        raise bad
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (not _HOSTNAME_RE.fullmatch(host) or not re.search(r"[A-Za-z]", labels[-1])
                or any(_NUMERIC_LABEL_RE.fullmatch(lab) for lab in labels)):
            raise bad from None
        return host.lower()
    mapped = getattr(ip, "ipv4_mapped", None)
    for a in (ip, mapped) if mapped else (ip,):
        if a.is_link_local or a.is_unspecified or a.is_multicast or a.is_reserved:
            raise QnxMcpError("invalid_argument", f"ssh host {host} is a link-local, unspecified, multicast or reserved address",
                              hint="That range holds the cloud metadata services; qnx-mcp never contacts it.")
    return str(ip)


@dataclasses.dataclass(frozen=True)
class SshConfig:
    """What the registration file of an SSH-only target holds. Validated again every time it is read."""
    host: str
    port: int
    user: str
    arch: str
    key: str | None  # absolute path; None: QNX_MCP_SSH_KEY
    fingerprint: str | None  # SHA256:... the host key had to have at registration; None: pinned at first contact

    def to_json(self) -> bytes:
        return (json.dumps({"kind": KIND_SSH, **dataclasses.asdict(self)}, indent=1) + "\n").encode()


def validate_ssh_config(host: Any, port: Any, user: Any, arch: Any, key: Any, fingerprint: Any) -> SshConfig:
    host = check_ssh_host(host)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise QnxMcpError("invalid_argument", f"invalid ssh port {port!r}", hint="Use 1-65535.")
    if not isinstance(user, str) or not _SSH_USER_RE.fullmatch(user):
        raise QnxMcpError("invalid_argument", f"invalid login user {user!r}", hint="Use a lowercase user name, e.g. qnxuser.")
    check_arch(arch)
    if key is not None:
        if not isinstance(key, str) or not key.startswith("/") or "\0" in key or "\n" in key:
            raise QnxMcpError("invalid_argument", f"ssh key {key!r} must be an absolute path", hint="Or omit it to use QNX_MCP_SSH_KEY.")
        why = ssh_key_problem(Path(key))
        if why:
            raise QnxMcpError("invalid_argument", f"the ssh key cannot be passed to ssh: {why}", hint="Use a key path without '%' or '$'.")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint)):
        raise QnxMcpError("invalid_argument", f"invalid host key fingerprint {fingerprint!r}",
                          hint="Use the SHA256 form that ssh-keygen -l prints, e.g. SHA256:abc... (43 base64 characters).")
    return SshConfig(host, port, user, arch, key, fingerprint)


def load_ssh_config(path: Path) -> SshConfig:
    """Read a registration file: a small regular file (never a link) that validates. Anything else fails closed."""
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
            raise ValueError("not a small regular file")
        d = json.loads(path.read_text())
        if not isinstance(d, dict) or d.get("kind") != KIND_SSH:
            raise ValueError("kind is not ssh")
        return validate_ssh_config(d.get("host"), d.get("port"), d.get("user"), d.get("arch"), d.get("key"), d.get("fingerprint"))
    except (OSError, ValueError, QnxMcpError) as e:
        raise QnxMcpError("config_error", f"the SSH-only registration {path} is not valid: {e}",
                          hint="Remove the target with target_unregister (or delete its directory) and register it again.") from None


def pinned_fingerprints(known_hosts: Path) -> list[str]:
    """SHA256 fingerprints of the host keys in a known_hosts file, as ssh-keygen -l prints them."""
    if shutil.which("ssh-keygen") is None:
        raise QnxMcpError("tool_missing", "ssh-keygen not found on this host", hint="Install openssh-client.")
    r = run(["ssh-keygen", "-l", "-f", str(known_hosts)], timeout=15)
    out = []
    for line in r.out.splitlines():
        fields = line.split()
        if r.returncode == 0 and len(fields) >= 2 and FINGERPRINT_RE.fullmatch(fields[1]):
            out.append(fields[1])
    return out


def machine_matches_arch(machine: str, arch: str) -> bool:
    """False only on a clear contradiction between the target's own `uname -m` and the registered arch
    (aarch64le registered on an x86 machine or the reverse); anything else is left to the ELF check at deploy time."""
    m = machine.lower()
    if arch == "aarch64le":
        return "x86" not in m
    return "aarch64" not in m and "arm" not in m


def _is_target_dir(d: Path) -> bool:
    return (d / "local" / "options").is_file() or os.path.lexists(d / SSH_TARGET_FILE)


class Target:
    def __init__(self, cfg: Config, name: str, *, must_exist: bool = True):
        self._moved: dict[str, str] = {}  # stale address -> address that answered after rediscovery
        self._ssh: SshConfig | None = None  # set for SSH-only targets
        self._registering = False  # first contact of target_register: the one time a host key may be accepted
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
        qemu_dir = (self.dir / "local" / "options").is_file()
        if os.path.lexists(self.dir / SSH_TARGET_FILE):
            if qemu_dir:
                raise QnxMcpError("target_not_allowed", f"{self.dir} holds both an mkqnximage image and an SSH-only registration",
                                  hint="Remove one of them; qnx-mcp will not guess which machine is meant.")
            self._ssh = load_ssh_config(self.dir / SSH_TARGET_FILE)
        if must_exist and not qemu_dir and self._ssh is None:
            raise QnxMcpError(
                "target_not_found",
                f"no target named {name!r} in {cfg.targets_dir}",
                hint="Call target_list to see targets, target_create to build a QEMU one or target_register for a machine reachable over SSH.",
            )

    # ------------------------------------------------------------ metadata

    @property
    def kind(self) -> str:
        """qemu: an mkqnximage guest this server builds, boots and stops. ssh: an existing machine reached over SSH only."""
        return KIND_SSH if self._ssh is not None else KIND_QEMU

    @property
    def login_user(self) -> str:
        return self._ssh.user if self._ssh is not None else LOGIN_USER

    @property
    def ssh_key(self) -> Path:
        return Path(self._ssh.key) if self._ssh is not None and self._ssh.key else self.cfg.ssh_key

    def require_qemu(self, tool: str, hint: str) -> None:
        """Refuse a VM lifecycle tool on an SSH-only target: qnx-mcp did not build the machine and cannot power it."""
        if self._ssh is not None:
            raise QnxMcpError(
                "target_kind_unsupported",
                f"{tool} manages a QEMU guest built by mkqnximage, but {self.name} is an SSH-only target "
                f"({self._ssh.user}@{self._ssh.host}:{self._ssh.port}), which qnx-mcp cannot power on, power off or delete",
                hint=hint,
            )

    @property
    def options(self) -> dict[str, str]:
        return _read_options(self.dir / "local" / "options")

    @property
    def arch(self) -> str:
        if self._ssh is not None:
            return self._ssh.arch
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
        if self._ssh is not None:
            return None
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

    def _getip(self, timeout: float) -> str | None:
        r = run([sdk_tool(sdk_env(self.cfg), "mkqnximage"), "--getip"], timeout=timeout, env=sdk_env(self.cfg), cwd=self.dir)
        for line in reversed(r.out.splitlines()):
            with contextlib.suppress(ValueError):
                return str(ipaddress.IPv4Address(line.strip()))
        return None

    def candidates(self, getip: bool, getip_timeout: float = 25) -> list[str]:
        """Addresses to try, newest live DHCP lease for this target's MAC first, then (if asked) mkqnximage --getip
        (arp). No port is probed: sshd on the QNX 8.0 images (OpenSSH 10) penalises connections that never
        authenticate and then drops every connection from the host for a while."""
        mac = self.mac or ""
        if not mac or self._ssh is not None:  # an SSH-only machine has one fixed address: nothing to rediscover
            return []
        try:
            text = self.cfg.leases.read_text()
        except OSError:
            text = "[]"
        ips = parse_leases(text, mac, time.time())
        if getip:
            found = self._getip(getip_timeout)
            if found and found not in ips:
                ips.append(found)
        return ips

    def lookup_ip(self, fallback: bool = True, getip_timeout: float = 25) -> str | None:
        """Current IPv4 address: the newest live lease, else mkqnximage --getip. A stale lease shows up as an ssh
        connection failure, after which the address is rediscovered and the command retried once (_run_ssh).
        Identity is not taken from the address: the pinned host key is what proves which machine answered."""
        if self._ssh is not None:
            return self._ssh.host
        if not self.mac:
            return None
        ips = self.candidates(getip=False)
        if ips:
            return ips[0]
        return self._getip(getip_timeout) if fallback else None

    def _run_ssh(self, make: Callable[[str], list[str]], ip: str | None, before_retry: Callable[[], None] | None = None, **kw: Any) -> ProcResult:
        """Run an ssh or scp command line built for an address. If the connection could not be set up, so the
        remote command never started, rediscover the address (leases and arp) and retry once at a new one."""
        ip = ip or self.require_ip()
        ip = self._moved.get(ip, ip)
        r = run(make(ip), **kw)
        if not _failed_before_command(r):
            return r
        new = next((c for c in self.candidates(getip=True, getip_timeout=10) if c != ip), None)
        if new is None:
            return r
        if before_retry is not None:
            before_retry()
        r = run(make(new), **kw)
        if not _failed_before_command(r):
            self._moved[ip] = new
        return r

    # ------------------------------------------------------------ host keys and ssh

    def image_host_keys(self) -> list[str]:
        """Host public keys that mkqnximage generated on this host (--sshd-pregen=yes, its default)."""
        keys: list[str] = []
        if self._ssh is not None:
            return keys
        for kind in ("ed25519", "rsa"):
            with contextlib.suppress(OSError):
                fields = (self.dir / "local" / "misc_files" / f"ssh_host_{kind}_key.pub").read_text().split()
                if len(fields) >= 2 and re.fullmatch(r"ssh-[a-z0-9-]+", fields[0]) and re.fullmatch(r"[A-Za-z0-9+/=]+", fields[1]):
                    keys.append(f"{fields[0]} {fields[1]}")
        return keys

    def host_key_policy(self) -> str:
        if self._ssh is not None:
            return "fingerprint" if self._ssh.fingerprint else "first_use"
        if self.image_host_keys():
            return "image"
        return "first_use" if self.cfg.trust_first_use else "unverifiable"

    def pin_host_keys(self) -> str:
        """Pin the image's own host keys under this target's HostKeyAlias. Images without
        pre-generated keys are pinned on first contact and kept until target_create/destroy."""
        if self._ssh is not None:
            return self.host_key_policy()  # pinned by target_register, never rewritten from here
        keys = self.image_host_keys()
        if keys:
            write_private(self.known_hosts, "".join(f"{self.alias} {k}\n" for k in keys).encode())
            return "image"
        return "first_use"

    def _ssh_opts(self) -> list[str]:
        why = ssh_path_problem(self.known_hosts)
        if why:
            raise QnxMcpError("internal_error", f"the host-key pin file cannot be passed to ssh: {why}", hint=STATE_DIR_FIX)
        why = ssh_key_problem(self.ssh_key)
        if why:
            raise QnxMcpError("internal_error", f"the ssh key cannot be passed to ssh: {why}", hint=SSH_KEY_FIX)
        private_dir(self.state)
        keys = self.image_host_keys()
        try:
            have = self.known_hosts.read_text()
        except OSError:
            have = ""
        extra: list[str] = []
        if self._ssh is not None:
            if not self.ssh_key.is_file():
                raise QnxMcpError("ssh_key_missing", f"ssh key {self.ssh_key} of target {self.name} not found",
                                  hint="Restore the key file, or target_unregister and target_register again with another key.")
            pinned = any(line.startswith(self.alias + " ") for line in have.splitlines())
            if pinned:
                strict = "yes"
            elif self._registering or (self._ssh.fingerprint is None and self.cfg.trust_first_use):
                strict = "accept-new"  # target_register's first contact (its fingerprint is checked right after), or first-use policy
            else:
                raise QnxMcpError(
                    "host_key_missing",
                    f"the pinned host key of target {self.name} is missing, so the machine's identity cannot be verified",
                    hint="target_unregister and target_register it again (give host_key_fingerprint); qnx-mcp does not re-pin a fingerprint target silently.",
                )
            extra = ["-o", f"Port={self._ssh.port}", "-o", "HashKnownHosts=no", "-o", "PreferredAuthentications=publickey",
                     "-o", "ForwardAgent=no", "-o", "ForwardX11=no"]
        elif keys:
            # The image's key files on this host are the source of truth; re-pin if the file drifted.
            want = "".join(f"{self.alias} {k}\n" for k in keys)
            if have != want:
                write_private(self.known_hosts, want.encode())
            strict = "yes"
        elif not self.cfg.trust_first_use:
            raise QnxMcpError(
                "host_key_missing",
                f"target {self.name} has no host keys generated by mkqnximage (local/misc_files/ssh_host_*_key.pub), so its identity cannot be verified",
                hint="Rebuild it with target_create (which uses --sshd-pregen=yes), or set QNX_MCP_TRUST_FIRST_USE=1 to pin whatever key answers first (weaker).",
            )
        else:
            strict = "yes" if any(line.startswith(self.alias + " ") for line in have.splitlines()) else "accept-new"
        return [
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={10 if self._ssh is not None else 5}",
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
            "-i", str(self.ssh_key),
            *extra,
        ]

    def require_ip(self) -> str:
        if self._ssh is not None:
            return self._ssh.host
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
        r = self._run_ssh(lambda a: ["ssh", *self._ssh_opts(), f"{self.login_user}@{a}", "--", quote_argv(argv)], ip, timeout=timeout, hold_stdin=hold_stdin,
                          max_output=max_output)
        self._classify(r, what, timeout, check)
        return r

    def ssh_script(self, script: str, *, timeout: float, what: str, ip: str | None = None, check: bool = True) -> ProcResult:
        """Run a fixed shell script. Callers build `script` only from constants and quote_argv()."""
        r = self._run_ssh(lambda a: ["ssh", *self._ssh_opts(), f"{self.login_user}@{a}", "--", script], ip, timeout=timeout)
        self._classify(r, what, timeout, check)
        return r

    def put(self, local: Path, remote: str, *, timeout: float, ip: str | None = None) -> None:
        check_remote_path(remote)
        r = self._run_ssh(lambda a: ["scp", "-q", "-p", *self._ssh_opts(), str(local), f"{self.login_user}@{_scp_host(a)}:{remote}"], ip, timeout=timeout)
        self._classify(r, f"copy to {remote}", timeout, check=False)
        if r.returncode != 0:
            raise QnxMcpError("deploy_failed", f"scp to {self.name}:{remote} failed", stderr=tail(r.err, 800))

    def fetch(self, runner: str, remote: str, local: Path, size: int, *, timeout: float, ip: str | None = None) -> None:
        """Stream exactly `size` bytes of a remote regular file into a new local file (the caller
        creates it in a private directory and verifies the hash). The cap holds during the transfer."""
        check_remote_path(remote)
        with open(local, "xb") as f:
            def restart() -> None:
                f.seek(0)
                f.truncate()
            r = self._run_ssh(lambda a: ["ssh", *self._ssh_opts(), f"{self.login_user}@{a}", "--", quote_argv([runner, "-X", remote, "-L", str(max(1, size))])],
                              ip, before_retry=restart, timeout=timeout, max_output=size, stdout_file=f)
        self._classify(r, f"copy from {remote}", timeout, check=False)
        if r.returncode != 0 or r.streamed_over(size):
            raise QnxMcpError("copy_failed", f"copy of {self.name}:{remote} failed (exit {r.returncode})", stderr=tail(r.err, 800))

    # ------------------------------------------------------------ lifecycle

    def wait_ssh(self, timeout: float) -> str:
        self.require_qemu("wait_ssh", _SSH_ONLY_HINT)
        deadline = time.monotonic() + timeout
        last = "no IP yet"
        next_getip = time.monotonic() + 8  # the lease file usually wins; arp fallback every 10 s after that
        started = time.monotonic()
        seen = False
        while time.monotonic() < deadline or not seen:
            check_cancelled()
            if self.qemu_pid() is None:
                if seen or time.monotonic() - started > 5:  # QEMU may not have written its pid file yet
                    raise QnxMcpError("target_down", f"QEMU for {self.name} exited during boot", hint=f"See {self.log_path}.", log=self.log_tail())
                time.sleep(0.5)
                continue
            seen = True
            use_getip = time.monotonic() >= next_getip
            if use_getip:
                next_getip = time.monotonic() + 10
            for ip in self.candidates(getip=use_getip, getip_timeout=8):  # authenticated attempts only, no port probes
                r = run(["ssh", *self._ssh_opts(), f"{self.login_user}@{ip}", "--", "true"], timeout=10)
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
        if self._ssh is not None:
            raise QnxMcpError("target_exists", f"target {self.name} already exists (an SSH-only target)", hint="Pick a new name.")
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
                args = ["--type=qemu", f"--arch={arch}", f"--ssh-ident={pub}", "--sshd-pregen=yes", "--noprompt",
                        f"--ram={ram}", f"--cpu={cpus}", *opts, "--run=-h"]
                for attempt in (1, 2):
                    self._mkqnximage(args, timeout=1200)
                    self.pin_host_keys()
                    if attempt == 1 and not (self.dir / "output" / "disk-qemu").is_file():
                        # Seen on a shared build box while other mkqnximage builds ran: the build reported
                        # success but output/disk-qemu was missing and QEMU exited. One clean rebuild recovers.
                        self._stop_qemu()
                        self.create_retried = "mkqnximage reported success but output/disk-qemu was missing; rebuilt once with --clean"
                        args = ["--noprompt", "--clean", "--run=-h"]
                        continue
                    self.wait_ssh(boot_timeout)
                    break
            except BaseException as e:
                cleanup = self._remove(created)
                if isinstance(e, QnxMcpError):
                    e.details["cleanup"] = cleanup
                raise
        st = self.status(probe=True)
        if getattr(self, "create_retried", None):
            st["note"] = self.create_retried
        return st

    def _stop_qemu(self, timeout: float = 30) -> bool:
        """Stop QEMU only through the verified pidfd: no tool that signals a numeric pid is involved."""
        got = self.qemu_pidfd()
        if got is None:
            return False
        pid, fd = got
        exited = False
        try:
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, sig)
                if select.select([fd], [], [], wait)[0]:  # a pidfd becomes readable when the process exits
                    exited = True
                    break
        finally:
            os.close(fd)
        if not exited:  # keep the PID file: it is the only way to find the process again, and the image must stay
            raise QnxMcpError("internal_error", f"QEMU for {self.name} (pid {pid}) survived SIGKILL", hint="Check the process on the build host, then retry.")
        with contextlib.suppress(OSError, ValueError):
            pidfile = self.dir / "output" / "qemu.pid"
            if int(pidfile.read_text().strip()) == pid:
                pidfile.unlink()  # what mkqnximage --stop leaves behind too
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
        self.require_qemu("target_destroy", _SSH_ONLY_HINT + " To drop the registration, call target_unregister.")
        with self.lock(exclusive=True):
            was_running = self.qemu_pid() is not None
            done = self._remove(created_dir=False)
        return {"name": self.name, "destroyed": True, "was_running": was_running, "detail": done}

    def register(self, *, host: str, port: int, user: str, arch: str, key: str | None, fingerprint: str | None) -> dict[str, Any]:
        """Register an existing machine reachable over SSH as an SSH-only target. Nothing is installed on it.

        The host must be in the operator's QNX_MCP_SSH_HOSTS. Host key: with a fingerprint, the key the machine
        presents on first contact is pinned and must hash to it (else nothing is kept); without one, the first key is
        pinned as for QEMU images without pre-generated keys, which needs QNX_MCP_TRUST_FIRST_USE=1. After this
        the pin is checked strictly (StrictHostKeyChecking=yes) on every call."""
        conf = validate_ssh_config(host, port, user, arch, key, fingerprint)
        if not self.cfg.ssh_hosts:
            raise QnxMcpError("ssh_host_not_allowed", "this server has no SSH host allowlist, so it refuses to register machines",
                              hint="Set QNX_MCP_SSH_HOSTS in the server's environment (comma-separated host globs such as 'qnx-ami.example.com'; '*' must be explicit).")
        if not self.cfg.ssh_host_allowed(conf.host):
            raise QnxMcpError("ssh_host_not_allowed", f"host {conf.host!r} is outside this server's SSH host allowlist {list(self.cfg.ssh_hosts)}",
                              hint="The person running qnx-mcp decides which machines it may log in to (QNX_MCP_SSH_HOSTS).")
        if conf.fingerprint is None and not self.cfg.trust_first_use:
            raise QnxMcpError("host_key_missing", "no host key fingerprint was given, so the machine's identity could not be verified",
                              hint="Pass host_key_fingerprint (SHA256:..., from ssh-keygen -l on the machine's key or the instance console output), "
                                   "or set QNX_MCP_TRUST_FIRST_USE=1 to pin whatever key answers first (weaker).")
        key_path = Path(conf.key) if conf.key else self.cfg.ssh_key
        if not key_path.is_file():
            raise QnxMcpError("ssh_key_missing", f"ssh key {key_path} not found", hint="Pass key (an absolute path) or set QNX_MCP_SSH_KEY; the machine must accept its public key.")
        with self.lock(exclusive=True):
            if os.path.lexists(self.dir):
                raise QnxMcpError("target_exists", f"target directory {self.dir} already exists", hint="Pick a new name, or target_unregister an SSH-only target first.")
            self.dir.mkdir(parents=True)
            with contextlib.suppress(FileNotFoundError):
                self.known_hosts.unlink()
            try:
                self._ssh = conf
                self._registering = True
                self.ssh_script("true", timeout=30, what="first contact")  # the only connection that may accept a new host key
                self._registering = False
                if conf.fingerprint is not None:
                    got = pinned_fingerprints(self.known_hosts)
                    if not got or any(g != conf.fingerprint for g in got):
                        raise QnxMcpError(
                            "host_key_mismatch",
                            f"the machine at {conf.host}:{conf.port} presented a host key that is not the one registered ({conf.fingerprint})",
                            hint="Check the address and the fingerprint; nothing was registered.", presented=got,
                        )
                write_private(self.dir / SSH_TARGET_FILE, conf.to_json())
                st = self.status(probe=True, _locked=True)
                if st.get("ssh") != "ok":
                    raise QnxMcpError("ssh_failed", f"target {self.name} registered but the status probe failed", probe=st.get("ssh"))
                if not machine_matches_arch(str(st.get("machine") or ""), conf.arch):
                    raise QnxMcpError("arch_mismatch", f"{conf.host} reports machine {st.get('machine')!r}, which is not {conf.arch}",
                                      hint="Register it with the other arch (x86_64 or aarch64le).")
            except BaseException as e:
                self._registering = False
                cleanup = self._remove(True)
                if isinstance(e, QnxMcpError):
                    e.details["cleanup"] = cleanup
                raise
        st["registered"] = True
        return st

    def unregister(self, before: Callable[[], None] | None = None) -> dict[str, Any]:
        """Forget an SSH-only target: its registration, pinned key and run records on this host. The machine is not touched.
        before runs once the exclusive lock is held (no run_start can be between its check and its record), and may refuse."""
        if self._ssh is None:
            raise QnxMcpError("target_kind_unsupported", f"{self.name} is a QEMU target; target_unregister only removes SSH-only registrations",
                              hint="Use target_destroy to delete a QEMU target.")
        with self.lock(exclusive=True):
            # this call may have waited for the lock while the name was unregistered and registered again
            path = self.dir / SSH_TARGET_FILE
            if not os.path.lexists(path):
                raise QnxMcpError("target_not_found", f"{self.name} was unregistered while this call waited", hint="Call target_list to see what is registered.")
            if load_ssh_config(path) != self._ssh:
                raise QnxMcpError("target_busy", f"the registration of {self.name} changed while this call waited for the target lock",
                                  hint="Call target_list, then call target_unregister again if you still mean to remove it.")
            if before is not None:
                before()
            detail = self._remove(created_dir=True)
        return {"name": self.name, "unregistered": True, "detail": detail,
                "note": f"{self._ssh.user}@{self._ssh.host} was not touched; programs and files qnx-mcp put on it (/tmp/qnx-mcp) stay there"}

    def start(self, boot_timeout: float) -> dict[str, Any]:
        self.require_qemu("target_start", _SSH_ONLY_HINT + " Check it with target_status.")
        with self.lock(exclusive=True):
            if self.qemu_pid() is None:
                self._mkqnximage(["--noprompt", "--run=-h"], timeout=600)
            self.pin_host_keys()
            self.wait_ssh(boot_timeout)
        return self.status(probe=True)

    def stop(self, timeout: float = 30) -> dict[str, Any]:
        self.require_qemu("target_stop", _SSH_ONLY_HINT)
        with self.lock(exclusive=True):
            was_running = self._stop_qemu(timeout)
            if was_running and not wait_until(lambda: self.qemu_pid() is None, 5):
                raise QnxMcpError("internal_error", f"QEMU for {self.name} survived SIGKILL")
        st = self.status(probe=False)
        st["was_running"] = was_running
        return st

    def status(self, probe: bool = True, _locked: bool = False) -> dict[str, Any]:
        opts = self.options
        pid = self.qemu_pid()
        ssh_only = self._ssh is not None
        st: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "state": ("registered" if ssh_only else "stopped") if pid is None else "running",
            "qemu_pid": pid,
            "arch": self.arch,
            "ram": opts.get("OPT_RAM"),
            "cpus": int(opts["OPT_CPU"]) if opts.get("OPT_CPU", "").isdigit() else None,
            "mac": opts.get("OPT_MACADDR"),
            "directory": str(self.dir),
            "host_key_policy": self.host_key_policy(),
            "ip": None,
            "privileges": {k: v for k, v in privileges(self.cfg, self.login_user).items() if k != "tools"},
        }
        if self._ssh is not None:
            st.update({"host": self._ssh.host, "port": self._ssh.port, "login_user": self._ssh.user})
        if (pid is None and not ssh_only) or not probe:
            return st
        with (contextlib.nullcontext() if _locked else self.lock(exclusive=False)):
            ip = self.lookup_ip()
            st["ip"] = ip
            if not ip:
                st["state"] = "booting"
                return st
            script = ("uname -a && date +%s && if test -e /proc/dumper; then echo dumper=yes; pidin -p dumper -F %A | sed -n 2p; "
                      "else echo dumper=no; fi")
            try:
                r = self.ssh_script(script, timeout=20, what="status probe", ip=ip)
                st["ip"] = self._moved.get(ip, ip)
                lines = r.out.splitlines()
                if len(lines) < 3 or not lines[1].strip().isdigit() or lines[2] not in ("dumper=yes", "dumper=no"):
                    raise QnxMcpError("remote_failed", "status probe output was not recognised", stdout=tail(r.out, 400))
                st["ssh"] = "ok"
                if ssh_only:
                    st["state"] = "running"  # an SSH-only machine's power state is not ours; it answered
                st["uname"] = lines[0]
                st["machine"] = lines[0].split()[-1] if lines[0].split() else None
                st["target_time"] = int(lines[1])
                st["dumper_running"] = lines[2] == "dumper=yes"
                dumper = lines[3].strip() if len(lines) > 3 else ""
                st["dumper_args"] = dumper or None
                st["core_naming"] = "sequential" if re.search(r"\s-[nN]\b", f" {dumper}") else "fixed"
            except QnxMcpError as e:
                # The ssh field reports what the target answered (or that it did not). A refusal made on this host before
                # ssh runs (unsafe state dir or key path, missing host keys or key) is the server's configuration, so it
                # fails the call with the same error every other tool returns.
                if e.code not in PROBE_ERRORS:
                    raise
                if ssh_only:
                    st["state"] = "unreachable"
                else:
                    st["state"] = "booting" if e.code in ("ssh_failed", "timeout") else st["state"]
                st["ssh"] = e.to_dict()
        return st


def _scp_host(host: str) -> str:
    """scp reads user@host:path, so an IPv6 literal needs brackets."""
    return f"[{host}]" if ":" in host else host


_SSH_ONLY_HINT = "Start and stop the machine with its own tooling (for example the AWS console or CLI); qnx-mcp only logs in to it."

PROBE_ERRORS = ("ssh_failed", "timeout", "host_key_mismatch", "remote_failed")


def list_targets(cfg: Config) -> list[dict[str, Any]]:
    out = []
    if not cfg.targets_dir.is_dir():
        return out
    for d in sorted(cfg.targets_dir.iterdir()):
        if d.is_symlink() or not _is_target_dir(d) or not cfg.allowed(d.name):
            continue
        try:
            out.append(Target(cfg, d.name).status(probe=False))
        except QnxMcpError:
            continue
    return out


_BEFORE_COMMAND = ("ssh: connect to host", "Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED", "kex_exchange_identification")


def _failed_before_command(r: ProcResult) -> bool:
    """ssh (or scp) exit 255 with an error from connection setup: the remote command did not start, so a retry
    cannot run it twice. A session that broke later ('Connection to ... closed') is never retried."""
    return r.returncode == 255 and not r.timed_out and any(m in r.err for m in _BEFORE_COMMAND)


def gdb_for(arch: str) -> str:
    return f"{ARCHES[arch][1]}-gdb"
