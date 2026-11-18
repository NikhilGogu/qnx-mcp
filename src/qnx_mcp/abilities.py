# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Which identity each tool acts with, and what it needs on the target.

environment_check and target_status report this table, so an agent or a person can see which tools
need root on the target and which run the agent's own program under QNX_MCP_RUN_AS.

Identities:
  server_user   the host user running qnx-mcp (no target access)
  login_user    the SSH login on the target ("root" on mkqnximage targets, the registered user, for example qnxuser,
                on an SSH-only target) and the runner it starts
  agent_program the program the agent built or named: QNX_MCP_RUN_AS uid:gid when set, else login_user

"verified_on_target" says whether that row has been exercised on a QNX target with the program dropped
to a non-root uid. The end-to-end tests so far ran with root login and root programs only, so every
agent_program row is False until tests/test_nonroot_e2e.py has passed on a target.
An SSH-only target with a non-root login has not been exercised either: every target row is False for it until
the live suite has passed against one (README, "SSH-only targets"). The runner then cannot drop privileges, so
QNX_MCP_RUN_AS is refused for such a target and the program runs as the login user.
"""

from __future__ import annotations

from typing import Any

from .util import Config

LOGIN_USER = "root"  # target.py logs in as root on mkqnximage targets

_HOST = {"side": "host", "identity": "server_user"}

TOOL_ABILITIES: dict[str, dict[str, Any]] = {
    "target_list": {**_HOST, "needs": ["read the targets directory"]},
    "target_create": {**_HOST, "needs": ["QNX SDP (mkqnximage)", "QEMU and KVM", "SSH to the new target as the login user"]},
    "target_register": {**_HOST, "needs": ["a host in QNX_MCP_SSH_HOSTS", "SSH to the machine as the registered user, key-only", "write the targets directory"]},
    "target_unregister": {**_HOST, "needs": ["write the targets directory (the machine is not contacted)"]},
    "target_start": {**_HOST, "needs": ["QEMU and KVM", "SSH to the target as the login user", "a QEMU target (refused for SSH-only targets)"]},
    "target_stop": {**_HOST, "needs": ["signal the target's QEMU process", "a QEMU target (refused for SSH-only targets)"]},
    "target_destroy": {**_HOST, "needs": ["write the targets directory", "a QEMU target (refused for SSH-only targets)"]},
    "build": {**_HOST, "needs": ["QNX SDP (qcc, make or cmake)", "host paths inside the workspace"]},
    "environment_check": {**_HOST, "needs": []},
    "target_status": {"side": "target", "identity": "login_user", "needs": ["run uname, date and pidin", "see /proc/dumper"]},
    "deploy_and_run": {"side": "target", "identity": "agent_program",
                       "needs": ["login user: write /tmp/qnx-mcp", "runner: dumper client (/proc/dumper) for the core path",
                                 "runner: setgid and setuid when QNX_MCP_RUN_AS is set (root)"]},
    "run_start": {"side": "target", "identity": "agent_program",
                  "needs": ["login user: write /tmp/qnx-mcp", "runner: dumper client (/proc/dumper) for the core path",
                            "runner: setgid and setuid when QNX_MCP_RUN_AS is set (root)"]},
    "run_poll": {"side": "target", "identity": "login_user", "needs": ["read the run directory (mode 0711, files 0600 for non-root runs)"]},
    "run_stop": {"side": "target", "identity": "login_user", "needs": ["write the run's control FIFO"]},
    "get_crash": {"side": "target", "identity": "login_user", "needs": ["read the core in the run directory", "host gdb from the QNX SDP"]},
    "file_put": {"side": "target", "identity": "login_user", "needs": ["write the destination below the allowed roots"]},
    "file_get": {"side": "target", "identity": "login_user", "needs": ["read the source file"]},
    "file_list": {"side": "target", "identity": "login_user", "needs": ["read the directory"]},
    "process_stop": {"side": "target", "identity": "login_user",
                     "needs": ["read /proc/<pid>", "signal a process qnx-mcp did not start (root, or the same uid)"]},
    "processes": {"side": "target", "identity": "login_user", "needs": ["run pidin"]},
    "logs": {"side": "target", "identity": "login_user", "needs": ["run slog2info and read the slog2 buffers"]},
}


def privileges(cfg: Config, login_user: str = LOGIN_USER) -> dict[str, Any]:
    """The identities in use and the per-tool table, for environment_check and target_status.
    login_user is the target's SSH login (default: the mkqnximage image's root)."""
    root_login = login_user == LOGIN_USER
    if cfg.run_as_error:
        program, run_as = "refused", None
    elif cfg.run_as and not root_login:
        program, run_as = "refused", None  # the runner needs root to drop to another uid
    elif cfg.run_as:
        program, run_as = "run_as", {"uid": cfg.run_as[0], "gid": cfg.run_as[1]}
    else:
        program, run_as = "login_user", None
    tools = []
    for name, a in TOOL_ABILITIES.items():
        row: dict[str, Any] = {"tool": name, **a}
        if a["side"] == "target":
            # Root login and root programs: the end-to-end tests (v0.3.0). Non-root programs: not yet run on a target.
            row["verified_on_target"] = root_login and not (a["identity"] == "agent_program" and run_as is not None)
        tools.append(row)
    out: dict[str, Any] = {"login_user": login_user, "program_identity": program, "run_as": run_as, "tools": tools}
    if cfg.run_as_error:
        out["error"] = cfg.run_as_error
    elif cfg.run_as and not root_login:
        out["error"] = f"QNX_MCP_RUN_AS needs a root login on the target, but the login is {login_user}; programs there run as {login_user}"
    return out


def describe_program_identity(cfg: Config) -> str:
    if cfg.run_as_error:
        return f"{cfg.run_as_error}; every run is refused"
    if cfg.run_as:
        return (f"agent programs run as uid {cfg.run_as[0]} gid {cfg.run_as[1]} (QNX_MCP_RUN_AS); "
                f"the runner and the other target tools act as {LOGIN_USER}; SSH-only targets with another login refuse runs while it is set")
    return (f"agent programs run as {LOGIN_USER}, the login user (QNX_MCP_RUN_AS is not set); "
            "on an SSH-only target they run as that target's login user")
