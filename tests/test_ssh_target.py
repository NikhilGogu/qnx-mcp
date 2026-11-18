# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""SSH-only targets (an existing QNX 8.0 machine reachable only over SSH, such as QNX's QNX OS 8.0 AMI), offline:
registration validation, the host allowlist, host-key pinning, refusal of the VM lifecycle tools, kind in the
listings, the cross-compile variant per architecture and the non-root login. ssh itself is replaced by a fake
that behaves like OpenSSH's host-key checking; the live counterpart is test_ssh_target_e2e.py."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import types
from pathlib import Path
from typing import Any

import pytest

from qnx_mcp import ops, sdk
from qnx_mcp import target as target_mod
from qnx_mcp.abilities import privileges
from qnx_mcp.target import (
    SSH_TARGET_FILE, Target, check_ssh_host, list_targets, load_ssh_config, machine_matches_arch, validate_ssh_config,
)
from qnx_mcp.util import Config, ProcResult, QnxMcpError

KEYGEN = shutil.which("ssh-keygen")
needs_keygen = pytest.mark.skipif(KEYGEN is None, reason="ssh-keygen not installed")
UNAME = "QNX qnxaws 8.0.0 2025/01/10-12:00:00EST nto aarch64le"
HOST = "qnx-ami.example.com"
BAD_FP = "SHA256:" + "A" * 43


def _cfg(tmp_path: Path, **env: str) -> Config:
    key = tmp_path / "id_test"
    key.write_text("not a real key\n")
    key.chmod(0o600)
    return Config.from_env({"QNX_MCP_TARGETS_DIR": str(tmp_path / "targets"), "QNX_MCP_STATE_DIR": str(tmp_path / "state"),
                            "QNX_MCP_ALLOW_TARGETS": "tb*,ami*", "QNX_MCP_WORKSPACE": str(tmp_path), "QNX_MCP_SSH_KEY": str(key), **env})


def _code(fn, *a, **kw) -> str:
    with pytest.raises(QnxMcpError) as e:
        fn(*a, **kw)
    return e.value.code


# ---------------------------------------------------------------- host and field validation

@pytest.mark.parametrize("host", ["qnx-ami.example.com", "ec2-3-4-5-6.eu-west-1.compute.amazonaws.com", "qnxbox", "10.0.0.7", "2001:db8::7",
                                  "QNX-AMI.Example.COM"])
def test_hosts_that_are_accepted(host):
    assert check_ssh_host(host) == host.lower()


@pytest.mark.parametrize("host", [
    "", "a b", "-oProxyCommand=x", "host:22", "user@host", "[::1]", "host/", "host;id", "$(id)", "a" * 254, ".example.com", "example..com",
    "169.254.1.1", "fe80::1", "0.0.0.0", "::", "224.0.0.1", "240.0.0.1", "::ffff:169.254.1.1",  # link-local, unspecified, multicast, reserved
    "2852039166", "0xa9fea9fe", "1.2.3", "251.254", "0x7f.example.com", "10.0.0.1.", None, 5,
])
def test_hosts_that_are_refused(host):
    assert _code(check_ssh_host, host) == "invalid_argument"


def test_fields_are_validated():
    ok = validate_ssh_config(HOST, 22, "qnxuser", "aarch64le", None, None)
    assert (ok.host, ok.port, ok.user, ok.arch, ok.key, ok.fingerprint) == (HOST, 22, "qnxuser", "aarch64le", None, None)
    for port in (0, 65536, -1, "22", True, None, 2.5):
        assert _code(validate_ssh_config, HOST, port, "qnxuser", "x86_64", None, None) == "invalid_argument", port
    for user in ("", "Root", "a b", "-x", "x;y", "a" * 40, None, "q$user"):
        assert _code(validate_ssh_config, HOST, 22, user, "x86_64", None, None) == "invalid_argument", user
    for arch in ("arm64", "x86", "", None):
        assert _code(validate_ssh_config, HOST, 22, "qnxuser", arch, None, None) == "invalid_argument", arch
    for key in ("relative/key", "~/.ssh/id", "/home/u/id%h", "/home/u/$HOME/id", "/k\nx", "/k\0x", 5):
        assert _code(validate_ssh_config, HOST, 22, "qnxuser", "x86_64", key, None) == "invalid_argument", key
    for fp in ("SHA256:short", "MD5:aa:bb", BAD_FP + "x", "sha256:" + "A" * 43, "SHA256:" + "A" * 42 + "!", 7):
        assert _code(validate_ssh_config, HOST, 22, "qnxuser", "x86_64", None, fp) == "invalid_argument", fp
    assert validate_ssh_config(HOST, 2222, "qnxuser", "x86_64", "/home/u/id_ed25519", BAD_FP).fingerprint == BAD_FP


def test_registration_file_fails_closed(tmp_path):
    good = validate_ssh_config(HOST, 22, "qnxuser", "aarch64le", None, BAD_FP)
    p = tmp_path / "ssh-target.json"
    p.write_bytes(good.to_json())
    assert load_ssh_config(p) == good
    variants = {
        "not json": b"{",
        "list": b"[]",
        "wrong kind": json.dumps({**json.loads(good.to_json()), "kind": "qemu"}).encode(),
        "bad host": json.dumps({**json.loads(good.to_json()), "host": "169.254.1.1"}).encode(),
        "bad port": json.dumps({**json.loads(good.to_json()), "port": 0}).encode(),
        "bad arch": json.dumps({**json.loads(good.to_json()), "arch": "mips"}).encode(),
        "too big": good.to_json() + b" " * 5000,
    }
    for why, data in variants.items():
        p.write_bytes(data)
        assert _code(load_ssh_config, p) == "config_error", why
    link = tmp_path / "link.json"
    p.write_bytes(good.to_json())
    link.symlink_to(p)
    assert _code(load_ssh_config, link) == "config_error"
    assert _code(load_ssh_config, tmp_path / "missing.json") == "config_error"


def test_machine_and_arch_are_compared_by_the_targets_own_uname():
    assert machine_matches_arch("aarch64le", "aarch64le") and machine_matches_arch("x86_64", "x86_64") and machine_matches_arch("x86pc", "x86_64")
    assert not machine_matches_arch("x86pc", "aarch64le") and not machine_matches_arch("aarch64le", "x86_64")
    assert machine_matches_arch("", "aarch64le") and machine_matches_arch("unknown", "x86_64")  # only a clear contradiction refuses


# ---------------------------------------------------------------- registration against a fake ssh

class FakeSsh:
    """Stands in for ssh in target.run: OpenSSH's StrictHostKeyChecking rules for one machine with one host key."""

    def __init__(self, hostkey: str, uname: str = UNAME):
        self.hostkey = hostkey  # "ssh-ed25519 AAAA..."
        self.uname = uname
        self.calls: list[list[str]] = []
        self.down = False

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        if argv[0] not in ("ssh", "scp"):
            return REAL_RUN(argv, **kw)
        self.calls.append(argv)
        opts = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in argv if "=" in a and not a.startswith("-") and a[0].isalpha()}
        known, alias, strict = Path(opts["UserKnownHostsFile"]), opts["HostKeyAlias"], opts["StrictHostKeyChecking"]
        res = lambda rc, out="", err="": ProcResult(argv, rc, out.encode(), err.encode(), False, 0.01)  # noqa: E731
        if self.down:
            return res(255, err="ssh: connect to host x port 22: Connection refused")
        pinned = [ln.split(None, 1)[1].strip() for ln in (known.read_text().splitlines() if known.exists() else []) if ln.startswith(alias + " ")]
        if pinned:
            if self.hostkey not in pinned:
                return res(255, err="Host key verification failed.")
        elif strict == "accept-new":
            known.write_text(f"{alias} {self.hostkey}\n")
        else:
            return res(255, err="No ED25519 host key is known for x and you have requested strict checking.\nHost key verification failed.")
        script = argv[-1]
        if script.startswith("uname"):
            return res(0, f"{self.uname}\n{int(time.time())}\ndumper=yes\n/proc/boot/dumper -n -d /data/var/dumper\n")
        return res(0)


REAL_RUN = target_mod.run


@pytest.fixture
def host_key(tmp_path):
    """A real ed25519 host key made by ssh-keygen: its public half and fingerprint."""
    if KEYGEN is None:
        pytest.skip("ssh-keygen not installed")
    k = tmp_path / "hostkey"
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True, capture_output=True)
    pub = " ".join(k.with_suffix(".pub").read_text().split()[:2])
    fp = subprocess.run([KEYGEN, "-l", "-f", str(k.with_suffix(".pub"))], check=True, capture_output=True, text=True).stdout.split()[1]
    return types.SimpleNamespace(pub=pub, fp=fp)


@pytest.fixture
def fake(monkeypatch, host_key):
    f = FakeSsh(host_key.pub)
    monkeypatch.setattr(target_mod, "run", f)
    return f


def _register(cfg: Config, name="ami1", **kw) -> dict[str, Any]:
    args: dict[str, Any] = dict(host=HOST, port=22, user="qnxuser", arch="aarch64le", key=None, fingerprint=None)
    args.update(kw)
    return Target(cfg, name, must_exist=False).register(**args)


@needs_keygen
def test_register_with_fingerprint_pins_and_reports(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS="qnx-*.example.com")
    st = _register(cfg, fingerprint=host_key.fp)
    assert st["registered"] is True and st["kind"] == "ssh" and st["state"] == "running" and st["ssh"] == "ok"
    assert (st["host"], st["port"], st["login_user"], st["arch"], st["machine"]) == (HOST, 22, "qnxuser", "aarch64le", "aarch64le")
    assert st["host_key_policy"] == "fingerprint" and st["privileges"]["login_user"] == "qnxuser"
    f = cfg.targets_dir / "ami1" / SSH_TARGET_FILE
    assert f.is_file() and (f.stat().st_mode & 0o777) == 0o600 and load_ssh_config(f).fingerprint == host_key.fp
    assert not (cfg.targets_dir / "ami1" / "local").exists()
    first, last = fake.calls[0], fake.calls[-1]
    assert "StrictHostKeyChecking=accept-new" in first and "StrictHostKeyChecking=yes" in last  # only the first contact may learn the key
    for call in fake.calls:
        assert "BatchMode=yes" in call and "PreferredAuthentications=publickey" in call and "ForwardAgent=no" in call
        assert "Port=22" in call and "HashKnownHosts=no" in call and f"qnxuser@{HOST}" in call
        assert "StrictHostKeyChecking=no" not in call and "UserKnownHostsFile=/dev/null" not in call
    # listings show the kind, without an ssh call
    n = len(fake.calls)
    [row] = list_targets(cfg)
    assert row["name"] == "ami1" and row["kind"] == "ssh" and row["state"] == "registered" and row["host"] == HOST and len(fake.calls) == n
    # later calls check the pin strictly and refuse another key
    t = Target(cfg, "ami1")
    assert t.status()["ssh"] == "ok"
    fake.hostkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherOtherOtherOtherOtherOtherOtherOtherOtherA"
    st = t.status()
    assert st["state"] == "unreachable" and st["ssh"]["code"] == "host_key_mismatch"


@needs_keygen
def test_register_refuses_a_key_that_does_not_match_the_fingerprint_and_leaves_nothing(tmp_path, fake):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    assert _code(_register, cfg, fingerprint=BAD_FP) == "host_key_mismatch"
    assert not (cfg.targets_dir / "ami1").exists()
    assert not (cfg.state_dir / "targets" / "ami1" / "known_hosts").exists()
    assert list_targets(cfg) == []


@needs_keygen
def test_register_with_first_use_needs_the_operators_switch(tmp_path, fake):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    assert _code(_register, cfg) == "host_key_missing" and fake.calls == []
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST, QNX_MCP_TRUST_FIRST_USE="1")
    st = _register(cfg)
    assert st["host_key_policy"] == "first_use" and st["ssh"] == "ok"
    assert Target(cfg, "ami1").status()["ssh"] == "ok"
    assert "StrictHostKeyChecking=yes" in fake.calls[-1]


@needs_keygen
def test_a_lost_pin_is_not_silently_replaced_for_a_fingerprint_target(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    _register(cfg, fingerprint=host_key.fp)
    (cfg.state_dir / "targets" / "ami1" / "known_hosts").unlink()
    n = len(fake.calls)
    with pytest.raises(QnxMcpError) as e:
        Target(cfg, "ami1").ssh(["true"], timeout=5)
    assert e.value.code == "host_key_missing" and len(fake.calls) == n


@needs_keygen
def test_register_is_refused_outside_the_operators_allowlist(tmp_path, fake, host_key):
    assert _code(_register, _cfg(tmp_path), fingerprint=host_key.fp) == "ssh_host_not_allowed"  # no QNX_MCP_SSH_HOSTS: refuses all
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS="other.example.com,10.*")
    assert _code(_register, cfg, fingerprint=host_key.fp) == "ssh_host_not_allowed"
    assert _code(_register, cfg, name="notallowed", host="10.1.2.3", fingerprint=host_key.fp) == "target_not_allowed"  # the target name list still applies
    assert fake.calls == [] and not cfg.targets_dir.exists() or not any(cfg.targets_dir.iterdir())
    # the allowlist is case-insensitive and a glob, never a substring
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS="QNX-AMI.EXAMPLE.COM")
    assert _register(cfg, fingerprint=host_key.fp)["registered"] is True


@needs_keygen
def test_register_validates_before_it_connects(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS="*")
    bad = [dict(host="169.254.1.1"), dict(port=70000), dict(user="Root"), dict(arch="riscv"), dict(fingerprint="SHA256:x"), dict(key="rel")]
    for kw in bad:
        assert _code(_register, cfg, fingerprint=kw.pop("fingerprint", host_key.fp), **kw) == "invalid_argument", kw
    assert _code(_register, cfg, key=str(tmp_path / "nokey"), fingerprint=host_key.fp) == "ssh_key_missing"
    assert fake.calls == [] and not (cfg.targets_dir / "ami1").exists()


@needs_keygen
def test_register_refuses_an_existing_name_and_a_wrong_arch_and_cleans_up(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    assert _code(_register, cfg, arch="x86_64", fingerprint=host_key.fp) == "arch_mismatch"  # the machine says aarch64le
    assert not (cfg.targets_dir / "ami1").exists() and not (cfg.state_dir / "targets" / "ami1" / "known_hosts").exists()
    fake.down = True
    assert _code(_register, cfg, fingerprint=host_key.fp) == "ssh_failed" and not (cfg.targets_dir / "ami1").exists()
    fake.down = False
    _register(cfg, fingerprint=host_key.fp)
    assert _code(_register, cfg, fingerprint=host_key.fp) == "target_exists"
    assert (cfg.targets_dir / "ami1" / SSH_TARGET_FILE).is_file()  # the refusal did not remove the existing registration


@needs_keygen
def test_a_directory_with_both_kinds_is_refused(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    _register(cfg, fingerprint=host_key.fp)
    (cfg.targets_dir / "ami1" / "local").mkdir()
    (cfg.targets_dir / "ami1" / "local" / "options").write_text("OPT_ARCH='x86_64'\n")
    assert _code(Target, cfg, "ami1") == "target_not_allowed"


# ---------------------------------------------------------------- lifecycle refusal, unregister

@needs_keygen
def test_vm_lifecycle_tools_refuse_ssh_targets_loudly(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    _register(cfg, fingerprint=host_key.fp)
    n = len(fake.calls)
    t = Target(cfg, "ami1")
    with pytest.raises(QnxMcpError) as e:  # create on an existing SSH-only name is a name clash, never a build
        t.create(arch="x86_64", ram="1G", cpus=1, options=[], boot_timeout=30)
    assert e.value.code == "target_exists"
    assert _code(t.start, 30) == "target_kind_unsupported" and _code(t.stop) == "target_kind_unsupported" and _code(t.destroy) == "target_kind_unsupported"
    assert "SSH-only" in str(_msg(t.destroy)) and "target_unregister" in str(_hint(t.destroy))
    assert len(fake.calls) == n and (cfg.targets_dir / "ami1" / SSH_TARGET_FILE).is_file()  # nothing was contacted or removed


def _msg(fn):
    with pytest.raises(QnxMcpError) as e:
        fn()
    return e.value.message


def _hint(fn):
    with pytest.raises(QnxMcpError) as e:
        fn()
    return e.value.hint


def test_unregister_refuses_a_qemu_target(tmp_path):
    cfg = _cfg(tmp_path)
    d = cfg.targets_dir / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:01'\n")
    assert _code(ops.unregister_target, cfg, "tb1") == "target_kind_unsupported"
    assert (d / "options").is_file()
    [row] = list_targets(cfg)
    assert row["kind"] == "qemu" and row["state"] == "stopped" and "host" not in row


@needs_keygen
def test_unregister_removes_only_the_registration_and_refuses_while_runs_are_in_flight(tmp_path, fake, host_key, monkeypatch):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    _register(cfg, fingerprint=host_key.fp)
    monkeypatch.setattr(ops, "_in_flight", lambda t: [{"run_id": "r1"}])
    assert _code(ops.unregister_target, cfg, "ami1") == "target_busy"
    assert (cfg.targets_dir / "ami1" / SSH_TARGET_FILE).is_file()
    monkeypatch.setattr(ops, "_in_flight", lambda t: [])
    n = len(fake.calls)
    r = ops.unregister_target(cfg, "ami1")
    assert r["unregistered"] is True and "not touched" in r["note"] and len(fake.calls) == n  # the machine is not contacted
    assert not (cfg.targets_dir / "ami1").exists() and not (cfg.state_dir / "targets" / "ami1" / "known_hosts").exists()
    assert _code(ops.unregister_target, cfg, "ami1") == "target_not_found"


# ---------------------------------------------------------------- ssh command lines, privileges, non-root login

@needs_keygen
def test_scp_brackets_an_ipv6_host_and_the_port_is_an_ssh_option(tmp_path, fake, host_key, monkeypatch):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS="*")
    _register(cfg, host="2001:db8::7", port=2222, fingerprint=host_key.fp)
    t = Target(cfg, "ami1")
    src = tmp_path / "f"
    src.write_text("x")
    t.put(src, "/tmp/qnx-mcp/f", timeout=5)
    argv = fake.calls[-1]
    assert argv[0] == "scp" and argv[-1] == "qnxuser@[2001:db8::7]:/tmp/qnx-mcp/f" and "Port=2222" in argv
    t.ssh(["true"], timeout=5)
    assert fake.calls[-1][-3:-1] == ["qnxuser@2001:db8::7", "--"] and "Port=2222" in fake.calls[-1]


def test_privileges_for_a_non_root_login(tmp_path):
    cfg = _cfg(tmp_path)
    root = privileges(cfg)
    assert root["login_user"] == "root"
    p = privileges(cfg, "qnxuser")
    assert p["login_user"] == "qnxuser" and p["program_identity"] == "login_user" and p["run_as"] is None
    assert all(t["verified_on_target"] is False for t in p["tools"] if t["identity"] == "agent_program")
    drop = privileges(_cfg(tmp_path, QNX_MCP_RUN_AS="1000:1000"), "qnxuser")
    assert drop["program_identity"] != "run_as" and "QNX_MCP_RUN_AS" in json.dumps(drop)  # it cannot drop privileges as that user


@needs_keygen
def test_run_as_is_refused_for_a_non_root_login(tmp_path, fake, host_key):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST, QNX_MCP_RUN_AS="1000:1000")
    _register(cfg, fingerprint=host_key.fp)
    t = Target(cfg, "ami1")
    with pytest.raises(QnxMcpError) as e:
        ops._run_as_argv(cfg, t)
    assert e.value.code == "config_error" and "QNX_MCP_RUN_AS" in e.value.message
    assert ops._run_as_argv(_cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST), t) == []


# ---------------------------------------------------------------- architecture of the build

class _Seen:
    def __init__(self):
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def run(self, argv, **kw):
        self.argvs.append(list(argv))
        self.envs.append(dict(kw.get("env") or {}))
        return ProcResult(list(argv), 1, b"", b"", False, 0.0)  # a failing build: only the command line matters here


@pytest.fixture
def seen(monkeypatch):
    s = _Seen()
    monkeypatch.setattr(sdk, "sdk_env", lambda cfg: {"QNX_HOST": "/sdp/host", "QNX_TARGET": "/sdp/target", "PATH": os.environ.get("PATH", "")})
    monkeypatch.setattr(sdk, "sdk_tool", lambda env, name: f"/sdp/bin/{name}")
    monkeypatch.setattr(sdk, "run", s.run)
    return s


@pytest.mark.parametrize("arch,variant", [("x86_64", "gcc_ntox86_64"), ("aarch64le", "gcc_ntoaarch64le")])
def test_build_uses_the_qcc_variant_of_the_architecture(tmp_path, seen, arch, variant):
    (tmp_path / "main.c").write_text("int main(void){return 0;}\n")
    r = sdk.build(_cfg(tmp_path), str(tmp_path / "main.c"), arch=arch)
    assert r["ok"] is False
    assert seen.argvs[0][:2] == ["/sdp/bin/qcc", f"-V{variant}"]
    (tmp_path / "Makefile").write_text("all:\n")
    sdk.build(_cfg(tmp_path), str(tmp_path), system="make", arch=arch)
    assert seen.envs[-1]["CC"] == f"/sdp/bin/qcc -V{variant}" and seen.envs[-1]["CXX"] == f"/sdp/bin/q++ -V{variant}_cxx"


@needs_keygen
def test_build_takes_the_architecture_from_the_target(tmp_path, fake, host_key, seen):
    cfg = _cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST)
    _register(cfg, fingerprint=host_key.fp)
    q = cfg.targets_dir / "tbq" / "local"
    q.mkdir(parents=True)
    (q / "options").write_text("OPT_ARCH='x86_64'\nOPT_MACADDR='52:54:00:00:00:02'\n")
    assert ops.build_arch(cfg, "ami1", None) == "aarch64le" and ops.build_arch(cfg, "ami1", "aarch64le") == "aarch64le"
    assert ops.build_arch(cfg, "tbq", None) == "x86_64"
    assert ops.build_arch(cfg, None, None) == "x86_64" and ops.build_arch(cfg, None, "aarch64le") == "aarch64le"
    assert _code(ops.build_arch, cfg, "ami1", "x86_64") == "arch_mismatch"
    assert _code(ops.build_arch, cfg, "nosuch", None) in ("target_not_found", "target_not_allowed")
    (tmp_path / "main.c").write_text("int main(void){return 0;}\n")
    sdk.build(cfg, str(tmp_path / "main.c"), arch=ops.build_arch(cfg, "ami1", None))
    assert seen.argvs[-1][1] == "-Vgcc_ntoaarch64le"


# ---------------------------------------------------------------- environment_check

def test_environment_check_marks_qemu_only_checks_optional_on_an_ssh_only_host(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "sdk_env", lambda cfg: (_ for _ in ()).throw(QnxMcpError("sdk_missing", "no sdp here")))
    r = ops.environment_check(_cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST))
    by = {c["check"]: c for c in r["checks"]}
    assert by["ssh_hosts"]["ok"] and HOST in by["ssh_hosts"]["detail"] and "tool:ssh-keygen" in by
    for name in ("kvm", "tool:qemu-system-x86_64", "dhcp_leases"):
        assert by[name].get("optional") is True and by[name]["applies_to"] == "qemu targets", name
    plain = {c["check"]: c for c in ops.environment_check(_cfg(tmp_path))["checks"]}
    assert not plain["tool:qemu-system-x86_64"].get("optional") and plain["tool:qemu-system-x86_64"]["applies_to"] == "qemu targets"
    assert "not set" in plain["ssh_hosts"]["detail"]
    # a QEMU target next to the SSH hosts makes the QEMU checks count again
    d = Path(_cfg(tmp_path).targets_dir) / "tb1" / "local"
    d.mkdir(parents=True)
    (d / "options").write_text("OPT_ARCH='x86_64'\n")
    both = {c["check"]: c for c in ops.environment_check(_cfg(tmp_path, QNX_MCP_SSH_HOSTS=HOST))["checks"]}
    assert not both["tool:qemu-system-x86_64"].get("optional")
