#!/usr/bin/env python3
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
"""ssh/scp/sftp wrapper for trial sessions: only the trial target is reachable.

Usage (from the per-trial wrappers): ssh_guard.py <ssh|scp|sftp> args...
Environment: EVAL_GUARD_ALLOW (allowed addresses, comma-separated; empty allows no host),
EVAL_GUARD_LOG (JSON lines log). Every call is logged; calls naming any other host are refused with exit 255.
"""
import json
import os
import sys
import time

REAL = {"ssh": "/usr/bin/ssh", "scp": "/usr/bin/scp", "sftp": "/usr/bin/sftp"}
VALUE_OPTS = {"ssh": set("BbcDEeFIiJLlmOoPpQRSWw"), "sftp": set("BbcDFiJloPRSs"), "scp": set("cDFiJlOoPSX")}


def host_of(dest: str) -> str:
    if dest.startswith("ssh://") or dest.startswith("scp://") or dest.startswith("sftp://"):
        dest = dest.split("://", 1)[1].split("/", 1)[0]
    host = dest.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


def destinations(tool: str, args: list[str]) -> tuple[list[str], list[str]]:
    hosts, notes, i, opts = [], [], 0, VALUE_OPTS[tool]
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:]):
                if ch in opts:
                    value = a[j + 2:] or (args[i + 1] if i + 1 < len(args) else "")
                    if not a[j + 2:]:
                        i += 1
                    if ch == "J":
                        hosts.append(host_of(value))
                    if ch == "o" and value.lower().replace(" ", "").startswith(("proxycommand", "proxyjump")):
                        notes.append("proxy option")
                    break
            i += 1
            continue
        if tool == "scp":
            if ":" in a and not a.startswith(("/", "./", "../")):
                hosts.append(host_of(a.split(":", 1)[0] if not a.startswith(("scp://", "[")) else a))
            i += 1
            continue
        hosts.append(host_of(a))
        break
    if tool == "scp":
        for a in args[i:]:
            if ":" in a and not a.startswith(("/", "./", "../")):
                hosts.append(host_of(a.split(":", 1)[0]))
    return hosts, notes


def main() -> None:
    tool, args = sys.argv[1], sys.argv[2:]
    # an empty allow list (arm C) refuses the target too, including the alias
    allow = {h for h in os.environ.get("EVAL_GUARD_ALLOW", "").split(",") if h}
    if allow:
        allow.add("target")
    hosts, notes = destinations(tool, args)
    ok = not notes and all(h in allow for h in hosts)
    try:
        with open(os.environ["EVAL_GUARD_LOG"], "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "tool": tool, "hosts": hosts, "allowed": ok, "notes": notes, "argv": args[:40]}) + "\n")
    except (KeyError, OSError):
        pass
    if not ok:
        bad = [h for h in hosts if h not in allow] or notes
        why = "only the target in TARGET.md is reachable from this session" if allow else "this session has no shell access to the target (see TARGET.md)"
        sys.stderr.write(f"{tool}: refused by the evaluation harness: {why} ({', '.join(bad)})\n")
        sys.exit(255)
    os.execv(REAL[tool], [tool] + args)


if __name__ == "__main__":
    main()
