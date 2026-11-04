# qnx-mcp

qnx-mcp is a Model Context Protocol (MCP) server that gives AI coding agents typed, bounded tools for QNX OS 8.0 development. An agent can boot QNX QEMU targets with mkqnximage, build with the QNX SDP toolchain, deploy and run programs with timeouts, turn dumper core files into source-level backtraces, and read `pidin` and `slog2info` output as JSON. It runs on the build host (where the SDP and QEMU live) and speaks MCP over stdio, locally or through `ssh -T`.

Status: v0.3.0. v0.1 was revised after two reviews; v0.2 added the tools an agent needs when it has no shell on the target (`file_list`, `process_stop`, and `remote_binary` for `deploy_and_run` and `run_start`) and v0.2.1 addressed their review (docs/DESIGN.md section 10). v0.3.0 follows the least-privilege study ("Least privilege, measured" below): `process_stop` handles services started with a relative path, and `remote_binary` refuses shells and system programs. Everything below was run against QNX OS 8.0 x86_64 QEMU targets; the evidence is in [docs/evidence](docs/evidence). Design notes and the security model in detail: [docs/DESIGN.md](docs/DESIGN.md).

Tutorial: the codelab [Give your AI coding agent a QNX target](https://nikhil-qnx-codelabs.pages.dev/qnx-mcp-agent-target/) (about 45 minutes; source in [codelab/](codelab/qnx-mcp-agent-target.md), dry runs in [codelab/DRYRUN.md](codelab/DRYRUN.md)). The agent skill file is [skills/qnx-mcp/SKILL.md](skills/qnx-mcp/SKILL.md).

## Why this is more than SSH

An agent with plain SSH has to compose shell commands, guess where cores land, pick the right gdb and sysroot, and parse free text. qnx-mcp does these things on the target and the host, and returns objects:

* **Native crash notification.** A small C helper on the target (`qnx-mcp-run`) registers a pulse with dumper (`DCMD_DUMPER_NOTIFYEVENT`) before the program starts. When the program dies from a signal that dumper handles, the helper waits for the pulse carrying that pid and asks `DCMD_DUMPER_GETPATH` for the exact core path, then moves the core into the run's own directory. Measured in the example below: the whole crashing run took 44 ms, and dumper's completion pulse was already waiting when the helper saw the program exit (`core_ready_ms: 0`).
* **Verified crash provenance.** Each run keeps a snapshot of the exact binary that was deployed. `get_crash` downloads the core with a size and SHA-256 check and refuses it (`core_mismatch`) unless the executable the core names is the run's content-addressed binary, the pid in the core is the run's pid, and the GNU build ID in the executable's note in the core's memory matches. A rebuilt binary, a binary without a build ID and a modified snapshot are all refused; this is tested.
* **Bounded runs.** Every run has a wall-clock deadline, capped output (truncation is reported), stdin on `/dev/null`, its own process group, and the whole group is killed when the program ends, so a forked child does not outlive the run. If the client cancels a call, the server kills its ssh and the helper notices the closed session and kills the program. Long-running programs get `run_start`, `run_poll` (output cursors) and `run_stop`.
* **Structured system state.** `processes` returns every thread with its QNX state (RECEIVE, REPLY, SEND, MUTEX, NANOSLEEP, ...), priority, policy and what it is blocked on; `get_crash` returns every thread's stack, so an agent can see which thread faulted and which threads were waiting on it.

## Example (real output, abridged)

From the v0.1 test run (`docs/evidence/test-run-v0.1.log`), the crash demo in `examples/crash_demo`:

`deploy_and_run(target="tb", binary=".../crash_demo")`
```json
{"run_id": "r-1680de50d5cd", "outcome": "crashed", "signal_name": "SIGSEGV", "stdout": "imu: average offset 6\n",
 "dumper": "notified", "core_path": "/tmp/qnx-mcp/runs/r-1680de50d5cd/crash_demo.core",
 "dumper_core_path": "/data/var/dumper/crash_demo.core", "remote_path": "/tmp/qnx-mcp/apps/9216f565e767f4b9/crash_demo",
 "duration_ms": 44, "core_ready_ms": 0, "group_clean": true,
 "next_step": "call get_crash with target='tb' and run_id='r-1680de50d5cd' for a source-level backtrace"}
```

`get_crash(target="tb", run_id="r-1680de50d5cd")`
```json
{"summary": "SIGSEGV (SEGV_MAPERR (address not mapped)) at fault address 0x0 in read_calibration at .../examples/crash_demo/crash.c:21; stack: read_calibration() <- average_offset() <- main(). Fault address 0x0 means a NULL pointer dereference.",
 "faulting_instruction": "=> 0x52c64838b1 <read_calibration+32>:\tmov    (%rax),%eax",
 "frames": [{"level": 0, "function": "read_calibration", "line": 21}, {"level": 1, "function": "average_offset", "line": 28}, {"level": 2, "function": "main", "line": 46}],
 "locals_frame0": [{"name": "s", "type": "const struct sensor *", "value": "0x3c26d9aab8", "arg": true}, {"name": "index", "type": "int", "value": "0", "arg": true}],
 "provenance": {"core_executable": "/tmp/qnx-mcp/apps/9216f565e767f4b9/crash_demo", "core_pid": 66793494, "core_build_id": "c65158d019120da0f95c3df1a466d88b", "build_id_match": true, "executable_match": true, "pid_match": true, "verified": true}}
```

The same call with `core_path` and a rebuilt `binary`:
```json
{"ok": false, "error": {"code": "core_mismatch", "message": "the core's executable has build ID c65158d019120da0f95c3df1a466d88b, the binary 0d42bd18fe0b4cb9ec2eeb2e1a83556c",
 "hint": "Pass the exact unstripped binary that crashed (same build), or use the run_id of the run that crashed."}}
```

## Requirements and tested versions

Tested on (commands and output in [docs/evidence/versions.txt](docs/evidence/versions.txt)):

| Component | Version |
|---|---|
| Build host | Ubuntu 22.04.5 LTS, x86_64, KVM, libvirt `virbr0` network |
| QNX SDP | 8.0 (gcc 12.2.0 targets, `ntox86_64-gdb` 14.2), non-commercial licence |
| Targets | QNX OS 8.0.0 x86_64 QEMU images built by mkqnximage |
| QEMU | 6.2.0 |
| Python | 3.10.12, `mcp` SDK 2.3.0 |
| OpenSSH client | 8.9p1 |
| Clients | Claude Code 2.1.312, Codex CLI 0.160.0 |

aarch64 builds work (`build(arch="aarch64le")` is tested); aarch64 targets are not.

## Install

```sh
git clone https://github.com/NikhilGogu/qnx-mcp.git && cd qnx-mcp
python3 -m venv ~/venv-qnx-mcp
~/venv-qnx-mcp/bin/pip install -e '.[test]'
QNX_MCP_ALLOW_TARGETS='dev*' ~/venv-qnx-mcp/bin/qnx-mcp    # MCP over stdio; normally your agent starts it
```

The target-side helper is compiled with the SDP's qcc on first use and uploaded to each target; nothing has to be installed on the target.

## Configure the server

Configuration is by environment variable:

| Variable | Default | Meaning |
|---|---|---|
| `QNX_MCP_ALLOW_TARGETS` | none (refuses every target) | Comma-separated globs of target names this server may touch, e.g. `dev*`; `*` must be explicit |
| `QNX_MCP_TARGETS_DIR` | `~/.qnx-mcp/targets` | One mkqnximage directory per target |
| `QNX_MCP_STATE_DIR` | `~/.qnx-mcp/state` | Locks, pinned host keys, run records, binary snapshots, cores (mode 0700) |
| `QNX_MCP_WORKSPACE` | not set | Colon-separated host directories; when set, every host path argument must resolve inside them |
| `QNX_MCP_SDP_ENV` | `~/qnx800/qnxsdp-env.sh` | The SDP environment script |
| `QNX_MCP_SSH_KEY` | `~/.ssh/id_ed25519` | Key installed into images by `target_create` |
| `QNX_MCP_DHCP_LEASES` | `/var/lib/libvirt/dnsmasq/virbr0.status` | Lease file used to find target addresses |
| `QNX_MCP_TRUST_FIRST_USE` | not set | `1` accepts targets without pre-generated host keys by pinning the first key seen (weaker) |
| `QNX_MCP_REMOTE_BINARY_ROOTS` | `/tmp:/data` | Colon-separated target directories below which `remote_binary` may start programs; set but empty refuses every `remote_binary`. Shells and `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system` are refused whatever it says |

Target creation takes about 20 s with KVM (22 s in the last lifecycle test run, `target_start` 15 s), so raise client tool timeouts where a client has them.

## Configure your agent

**Claude Code** (tested, transcript in [docs/evidence/claude-code-session.json](docs/evidence/claude-code-session.json)):
```sh
claude mcp add -s project qnx -e QNX_MCP_ALLOW_TARGETS='dev*' -- ~/venv-qnx-mcp/bin/qnx-mcp
```
With the SDK and QEMU on a separate build box, run the server there through ssh (this is the configuration that was tested):
```sh
claude mcp add -s project qnx -- ssh -T buildbox env "QNX_MCP_ALLOW_TARGETS='dev*'" /home/me/venv-qnx-mcp/bin/qnx-mcp
```

**Codex CLI** (tested, transcript in [docs/evidence/codex-session.json](docs/evidence/codex-session.json)), in `~/.codex/config.toml`:
```toml
[mcp_servers.qnx]
command = "ssh"
args = ["-T", "buildbox", "env", "QNX_MCP_ALLOW_TARGETS='dev*'", "/home/me/venv-qnx-mcp/bin/qnx-mcp"]
tool_timeout_sec = 600
```
The test passed the same keys as `-c mcp_servers.qnx.command=...` overrides to `codex exec`.

**Cursor** (untested), `.cursor/mcp.json`:
```json
{ "mcpServers": { "qnx": { "command": "/home/me/venv-qnx-mcp/bin/qnx-mcp", "env": { "QNX_MCP_ALLOW_TARGETS": "dev*" } } } }
```

**VS Code** (untested), `.vscode/mcp.json`:
```json
{ "servers": { "qnx": { "type": "stdio", "command": "/home/me/venv-qnx-mcp/bin/qnx-mcp", "env": { "QNX_MCP_ALLOW_TARGETS": "dev*" } } } }
```

## Tools

Every tool returns JSON as MCP `structuredContent` (and as text). Success has `ok: true`; failure sets `isError` and returns `{ok: false, error: {code, message, hint, details}}`. Each declared output schema is "success object or error object", so both conform.

| Tool | What it does |
|---|---|
| `environment_check` | SDP, qcc, mkqnximage, gdb, KVM, QEMU, ssh, key, lease file, allowlist and workspace checks, each with a fix |
| `target_list` | Allowed targets under the targets dir with state (no SSH) |
| `target_create` | mkqnximage build and headless boot, waits for SSH; pins the image's host keys; failed creates are removed |
| `target_start` / `target_stop` | Boot without rebuild / stop QEMU only through a pidfd of the verified QEMU process |
| `target_destroy` | Stop and delete a managed target (`confirm` must repeat the name) |
| `target_status` | State, IP, SSH probe, target clock, dumper arguments and core naming, host key policy |
| `build` | qcc, make or cmake with the SDP; `diagnostics[]` (file, line, column, severity, option), `artifacts[]` (arch, loader, build ID, debug info) |
| `deploy_and_run` | Snapshot, verified upload, one run with deadline and caps; outcome `exited`, `crashed`, `killed`, `timeout`, `stopped` or `exec_failed`. With `remote_binary` instead of `binary`: a program already on the target, below `/tmp` or `/data`, never a shell or a system program |
| `run_start` / `run_poll` / `run_stop` | Background runs with output cursors (`max_kb` up to 1024), lifetime limit, per-server ownership of run ids; stop goes through the owning runner's control FIFO. `run_start` also takes `remote_binary` |
| `get_crash` | Verified core, gdb/MI backtrace: faulting frame, first user frame, locals, source lines, every thread's stack. For a `remote_binary` run, `run_id` plus the host build as `binary` |
| `file_put` / `file_get` | One file each way, size and SHA-256 verified; no directory, symlink or symlinked-parent destinations; never replaces a file unless `overwrite` |
| `file_list` | One target directory, read-only: name, type, size, mode, mtime, symlink target (links are never followed); sorted by name, at most `max_entries` (up to 1000), truncation reported |
| `processes` | `pidin` as JSON, optionally per thread |
| `process_stop` | Stop a process qnx-mcp did not start, only if its executable is below `/tmp` or `/data` (the executable in `/proc/<pid>/exefile`, which also covers services started with a relative path, and its resolved path, checked on the target at call time); never pid 1, procnto, a critical process, the runner or anything in `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system`. SIGTERM (or another signal), then SIGKILL after `grace_s` if `kill_after_grace` |
| `logs` | `slog2info` as JSON, filtered by buffer, process, time, severity, text |

More real outputs (from the v0.1 test run, `docs/evidence/test-run-v0.1.log`):

`target_status(name="tb")`
```json
{"name": "tb", "state": "running", "ip": "192.168.122.130", "uname": "QNX noname 8.0.0 2026/06/05-16:26:03EDT x86pc x86_64",
 "dumper_running": true, "dumper_args": "dumper -U 30:30 -d /data/var/dumper -M 1000 -D cores", "core_naming": "fixed", "host_key_policy": "image"}
```

`build(path=".../bad.c")` (excerpt)
```json
{"ok": false, "error": {"code": "build_failed"}, "diagnostics": [
 {"file": ".../bad.c", "line": 4, "column": 16, "severity": "error", "message": "'y' undeclared (first use in this function)", "source": "gcc-json"},
 {"file": ".../bad.c", "line": 3, "column": 9, "severity": "warning", "message": "unused variable 'unused'", "option": "-Wunused-variable", "source": "gcc-json"}]}
```

`run_start` of a program printing a line every 200 ms, then `run_poll`, `processes` while it runs, `run_poll` from the returned offset, `run_stop`:
```json
{"run_id": "r-1780d01f863c", "state": "running", "pid": 69701655, "remote_path": "/tmp/qnx-mcp/apps/38649b3e134f0afd/ticker"}
{"state": "running", "stdout": "tick 0\ntick 1\ntick 2\ntick 3\ntick 4\ntick 5\n", "stdout_next": 42}
{"pid": 69701655, "name": "ticker", "threads": [{"tid": 1, "priority": 10, "policy": "RR", "state": "NANOSLEEP", "blocked_on": null}]}
{"state": "running", "stdout_offset": 42, "stdout_next": 86}
{"was_running": true, "acknowledged": true, "state": "finished", "outcome": "stopped"}
```

`get_crash` of a program whose third thread dereferences NULL while the main thread waits in `pthread_join` and another thread waits on a mutex: `faulting_thread_id` is `"3"`, `faulting_frame.function` is `crasher`, and `threads[]` lists all three stacks with `faulting: true` on thread 3.

`file_get(target="tb", remote_path="/tmp/qnx-mcp-test/.../blob.bin.txt")`
```json
{"remote_path": "/tmp/qnx-mcp-test/157045-1793725678/blob.bin.txt", "local_path": "/home/ubuntu/.qnx-mcp/state/files/tb/69f50322b9a5/blob.bin.txt",
 "size": 20, "sha256": "e4a985feba6c291b0de2319ce53b41e44d6a1413c535c586a649e896ac623743", "mode": "0600", "text": "hello from the host\n"}
```

`logs(target="tb", process="sshd", since_s=3600, limit=3)` (one entry)
```json
{"total_entries": 1082, "matched": 6, "returned": 3, "entries": [{"time_iso": "2026-11-03T17:08:02.594Z", "process": "sshd_session", "pid": 71675906,
 "severity_name": "info", "message": "<38>Nov 03 17:08:02 sshd-session[71675906-1]: Connection closed by 192.168.122.1 port 49756 [preauth]"}]}
```

## Without a shell on the target (v0.2 and later)

An agent that has qnx-mcp and no shell can still look at directories, restart a program installed on the target and stop a hung one. Real outputs from `docs/evidence/test-run.log` (`tests/test_shellfree_e2e.py`), abridged:

`file_list(target="tb", path="/tmp/qnx-mcp-test/l1059391")` (`alink` and `boot` are symlinks the test made; they are described, not followed)
```json
{"path": "/tmp/qnx-mcp-test/l1059391", "real_path": "/data/var/tmp/qnx-mcp-test/l1059391", "real_path_verified": true, "complete": true, "total_entries": 4, "returned": 4, "truncated": false, "entries": [
 {"name": "a.txt", "type": "file", "size": 6, "mode": "0644", "mtime_iso": "2026-11-04T00:39:20Z"},
 {"name": "alink", "type": "symlink", "size": 5, "link_target": "a.txt"},
 {"name": "boot", "type": "symlink", "size": 10, "link_target": "/proc/boot"},
 {"name": "sub", "type": "dir", "size": 4096, "mode": "0755"}]}
```
With `max_entries=2` the same call returns `a.txt` and `alink` with `truncated: true` and `total_entries: 4`.

`deploy_and_run(target="tb", remote_binary="/tmp/qnx-mcp-test/r1059391/bin/echoargs", args=["a b", "$(id)"])`
```json
{"run_id": "r-a684f7a8aabe", "outcome": "exited", "exit_code": 7, "stdout": "[a b]\n[$(id)]\n", "remote_binary": true, "binary": null,
 "remote_path": "/tmp/qnx-mcp-test/r1059391/bin/echoargs", "binary_sha256": "b610c3606440f8f7c80590e8f4520b11396456b2277706e6c2a27d2ea05ae7d9"}
```
The crash demo installed on the target and run the same way crashes, and `next_step` says that `get_crash` needs `binary=<host path of the unstripped build of ...>`, because qnx-mcp holds no copy of a program it did not deploy. `get_crash(target="tb", run_id="r-60b0e584e5c8", binary=".../crash_demo")`:
```json
{"summary": "SIGSEGV (SEGV_MAPERR (address not mapped)) at fault address 0x0 in read_calibration at .../examples/crash_demo/crash.c:21; stack: read_calibration() <- average_offset() <- main(). ...",
 "provenance": {"source": "remote_run", "core_executable": "/tmp/qnx-mcp-test/r1059391/bin/crash_demo", "core_pid": 164540438, "pid_match": true, "executable_match": true,
  "core_build_id": "c65158d019120da0f95c3df1a466d88b", "build_id_match": true, "evidence": "build ID, executable path and pid match", "verified": true}}
```
A rebuilt binary is refused with `core_mismatch` (different build ID). The same program linked with `--build-id=none` still gets a backtrace, but with `"verified": false` and `"evidence": "pid and path match; build ID not available (the binary has none)"`: nothing ties that binary to the code that ran.

`process_stop(target="tb", pid=167637014)` of a program that was started outside qnx-mcp:
```json
{"pid": 167637014, "name": "hang", "ppid": 1, "start_ns": 1793752782325283512, "executable": "/tmp/qnx-mcp-test/p1059391/hang",
 "real_path": "/data/var/tmp/qnx-mcp-test/p1059391/hang", "signal": "SIGTERM", "signal_sent": true, "sigkill_sent": false, "exited": true, "still_running": false}
```
A program that ignores SIGTERM returns `exited: false, still_running: true` after `grace_s=1`, with a `next_step`; the same call with `kill_after_grace=true` returns `sigkill_sent: true, exited: true`. Refusals are `process_not_allowed` errors with a hint; the messages from the log:
```
pid 1: pid 1 is procnto, the QNX microkernel and process manager
./sleep started in /system/bin: its executable /system/bin/sleep is in /system, a system directory
slogger2: its executable /proc/boot/slogger2 is in /proc/boot, a system directory
/system/bin/sleep: its executable /system/bin/sleep is in /system, a system directory
a symlink in /tmp to /system/bin/sleep: it is in a system directory (/tmp/qnx-mcp-test/p1059391/sleeplink resolves to /system/bin/sleep)
the runner of a background run: it is qnx-mcp's own runner, which enforces the deadline of a run; use run_stop for runs
```
Services started with a relative path, the way the evaluation's field services are (`cd dir && ./prog &`), are found through `/proc/<pid>/exefile`: for a program started as `./hang` the result has `"executable": "/data/var/tmp/qnx-mcp-test/p1059391/./hang", "real_path": "/data/var/tmp/qnx-mcp-test/p1059391/hang", "exited": true`.

`remote_binary` refuses shells and system programs, also through a link (`path_not_allowed`): `/proc/boot/ksh` "is refused: it is a shell"; `/system/bin/slay` "is in /system, a system directory"; a link in `/tmp` to `/proc/boot/ksh` "is in a system directory (it resolves to /proc/boot/ksh)".

A program that execs `/system/bin/sleep` when it gets SIGTERM, stopped with `grace_s=1, kill_after_grace=true`, returns an error with `details.signal_sent: true`: "SIGTERM was sent to process 169095190 and it did not exit; SIGKILL was not sent because its start time differs (the pid was reused, or the program called exec)".

## Least privilege, measured

The evaluation in [eval/](eval) (protocol preregistered in [eval/PROTOCOL.md](eval/PROTOCOL.md); tasks, harness, summaries and every trial transcript in [eval/results/raw.tar.xz](eval/results)) asked whether an agent with qnx-mcp and no shell on the target fixes the same QNX bugs as an agent with a root shell. Arm A had QNX's agent skills plus a root shell over SSH; arm C had the same skills plus qnx-mcp 0.2.1, and its ssh, scp and sftp refused the target (the agents kept a shell on the build host for their workspace). Eight seeded bugs whose fault needs evidence from the target (a buffer overflow, a deadlock across processes, a resource manager whose error shows in slog2, a stack overflow, a hang, a NULL dereference in a worker thread, a CMake cross-build, a file descriptor leak), 5 trials per task, arm and model, Claude Sonnet 5.5 and Haiku 5.5 in Claude Code 2.1.312: 160 trials, all scored ([summary](eval/results/least-privilege-2026-11-03/summary.md)).

| model | arm | oracle pass | root cause (blind judge) | median turns [range] | median cost USD [range] | median wall s |
|---|---|---|---|---|---|---|
| Sonnet 5.5 | A (root shell) | 40/40 | 39/40 | 10 [2-16] | 0.123 [0.087-0.194] | 42 |
| Sonnet 5.5 | C (qnx-mcp, no shell) | 40/40 | 40/40 | 20 [13-43] | 0.198 [0.128-0.354] | 51 |
| Haiku 5.5 | A (root shell) | 40/40 | 40/40 | 16 [10-44] | 0.019 [0.013-0.038] | 103 |
| Haiku 5.5 | C (qnx-mcp, no shell) | 40/40 | 39/40 | 37 [20-64] | 0.028 [0.021-0.051] | 115 |

Costs: without a shell the agents needed about twice the turns and paid 61 percent (Sonnet) and 47 percent (Haiku) more per trial at the median; paired by task and repetition, C minus A was +10 turns and +0.067 USD for Sonnet, +18 turns and +0.008 USD for Haiku. The study's agents cost 15.31 USD and all of phase 3 (smoke test, pilot, study, blind judge) 17.98 USD, as Claude Code's list-price estimates. No arm C agent tried ssh, scp or sftp, and qnx-mcp's own 3643 ssh and scp calls reached only the trial targets. Two gaps in qnx-mcp 0.2.1 showed up and are fixed in 0.3.0: `process_stop` refused services started with a relative path (23 of 80 arm C trials; the agents then checked their fix with their own copy of the service), and `remote_binary` would have started a shell or a system program as root (no agent did).

What it does not show:

* Both arms passed every trial, so it does not show that qnx-mcp makes agents better or more successful; with 40 of 40, each pass rate is only bounded above 0.91 (95 percent).
* Arm C's isolation was a PATH wrapper that refused ssh, scp and sftp to the target, plus a review of every transcript, not enforcement by the operating system: the agents ran as the same Unix user with a shell on the build host.
* One root-cause miss (Haiku, arm C, v2-02) is a judge parse failure: the judge returned no parsable verdict and it is counted as a miss, as recorded.
* The v2-04 oracle accepts a stack-size workaround: a Sonnet arm A agent enlarged the thread stack, which the judge rated as masking the unbounded recursion, and the oracle still passed.
* The tasks are our own small programs on QEMU, with one client and two models; costs are list-price estimates.

## Security model

The agent is treated as capable but fallible and possibly prompt-injected. The server enforces the limits; tool descriptions only explain them.

**What the v0.2 path rules are for.** The rules on `process_stop`, `file_list` and `remote_binary` protect against mistakes: an agent stopping a system process, starting qnx-mcp's own runner, or misreading where a directory is. They do not stop an agent that sets out to break them, because `deploy_and_run` already runs any program the agent builds, as root, and such a program can stop any process or change any file. Since v0.3.0 `remote_binary` refuses shells and system programs, so qnx-mcp itself does not hand a shell to an agent whose client keeps it away from the target's shell; an agent can still build a program that does what a shell does. The races that remain are listed under Limitations.

* **Targets.** Names must match `[a-z][a-z0-9_-]{0,31}` and the allowlist; an unset or empty allowlist refuses everything. A target directory that is a symlink, or resolves outside the targets dir, is refused. `target_destroy` only deletes mkqnximage directories directly inside the targets dir.
* **Target identity.** mkqnximage generates the image's SSH host keys on the host (`--sshd-pregen=yes`, its default; `target_create` passes it explicitly). They are stored in `local/misc_files/` of the target directory and copied to `/data/var/ssh` on the image's persistent data partition, so they survive reboots (tested: SSH with `StrictHostKeyChecking=yes` after stop and start). qnx-mcp pins them under `HostKeyAlias=qnx-mcp-<name>`, so an address change cannot switch machines unnoticed: a VM that does not present the pinned key gets `host_key_mismatch`. A target without pre-generated keys is refused (`host_key_missing`) unless `QNX_MCP_TRUST_FIRST_USE=1` is set, which pins whatever key answers first (weaker; not tested against a real image). Expired DHCP leases are ignored; `mkqnximage --getip` is the fallback.
* **No shell tool.** Remote commands are fixed argument lists; agent arguments are quoted one by one. A test passes `$(touch ...)`, `; touch ...`, backticks, quotes, `*`, a newline and U+2028 and checks that the program receives them unchanged.
* **Validated paths.** Remote paths must be normalised (no `//`, `.`, `..` or trailing `/`) and use `[A-Za-z0-9._+/-]` only. `file_put` writes only below `/tmp` or `/data` and never into qnx-mcp's own directory, also not through an alias such as `/data/var/tmp/qnx-mcp` (the runner compares real paths); parents are opened one by one without following symlinks, directories and symlinks are refused as destinations, and installation is atomic. `file_get` stages the download in the server state and installs it through a descriptor of the destination directory without changing that directory, never replacing a file or following a symlink unless `overwrite` is set for a regular file. With `QNX_MCP_WORKSPACE` set, every host path, and every source file read for backtraces (paths come from DWARF), must resolve inside it; source context is only read from C/C++ source files.
* **Listing.** `file_list` reads one directory anywhere on the target (as `file_get` reads any file) and never follows links inside it; the directory itself may be reached through a link. `real_path` is resolved separately and `real_path_verified` says whether it names the directory that was read (same device and inode, checked after reading). A failed `readdir` gives `complete: false`. Output is bounded (`max_entries` at most 1000, and the runner gives up after 30 s).
* **Programs already on the target.** `remote_binary` must be a normalised absolute path to an executable regular file below `/tmp` or `/data` (`QNX_MCP_REMOTE_BINARY_ROOTS`), outside `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system` and `/tmp/qnx-mcp`, and not named like a shell (`sh`, `ksh`, `bash`, QNX's `esh`, `fesh` and `uesh`, `toybox`, `busybox` and others). The host checks the path as given; the runner on the target checks it again by real path, before and just before the program starts, so a link in `/tmp` to `/proc/boot/ksh` is refused (tested). A crash is tied to a host binary only by the GNU build ID in the core; without one, `get_crash` still returns the backtrace but with `verified: false` and `evidence: "pid and path match; build ID not available"`.
* **Stopping processes.** `process_stop` first reads the process's identity: its start time in nanoseconds from its `/proc` entry (`DCMD_PROC_INFO`, the creation token), its executable from `/proc/<pid>/exefile` (documented by QNX as the path of the executable used to run the process; for a relative start it is the start directory joined with the relative path, tested), and the path pidin shows, which must name the same file. The runner then opens `/proc/<pid>/ctl`, requires the same start time, reads the executable again, resolves it, and signals through that descriptor. The executable, in normal form, and its resolved path must both be below `/tmp` or `/data` and outside `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system` and qnx-mcp's runner directory; pid 1, procnto and critical processes are refused, and so are a symlink in `/tmp` to a system program and `./sleep` started in `/system/bin` (tested). Right before each signal (SIGTERM, and SIGKILL after the grace period) the start time, the critical flag and the executable path are read again through the held descriptor and nothing is sent if they changed; on QNX 8.0 an `exec` gives the process a new start time (tested). A state that cannot be read is an error result that says which signal was sent, never `exited`.
* **Builds run code.** `build` runs the project's own make or cmake files, which can execute anything on the build host. Only point it at trees you trust.
* **Bounded execution.** Host commands run in their own process group with timeouts and capped output; cancelled calls kill their processes. Target programs have deadlines and output caps; cores are size-limited before download and after decompression.
* **Locking.** Per-target `flock`: exclusive for lifecycle changes, shared for runs, probes and inspection, so `processes` works during a run and `target_stop` waits for runs in progress (both tested). Waiting fails with `target_busy` after 120 s.
* **Privileges.** Programs run as root on the target, because mkqnximage development images log in as root. Use disposable development targets. The v0.2 tools do not add a shell: an agent still cannot run a command line, but a program it builds and runs can do anything root can.
* **Data.** Cores, logs, run records and binary snapshots stay in the state directory (mode 0700, files 0600) on the build host.

## Tests

```sh
cd tests
python -m pytest -q -p no:asyncio test_units.py test_mcp_offline.py test_skill.py  # no QNX needed
python -m pytest -q -p no:asyncio test_build_sdk.py                                # needs the QNX SDP
QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*' QNX_MCP_E2E_TARGET=tb \
  python -m pytest -q -s -p no:asyncio test_slice_e2e.py test_shellfree_e2e.py     # needs a running target
QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*' QNX_MCP_E2E_TARGET=tb QNX_MCP_E2E_CREATE=tb2 \
  python -m pytest -q -s -p no:asyncio test_lifecycle_e2e.py                       # builds, boots and destroys tb2
```

Last full run on the build box (v0.3.0, 4 Nov 2026, with `QNX_MCP_TARGETS_DIR` set to the box's targets directory): 92 passed in 161.1 s (67 unit, 5 offline MCP, 2 skill, 3 SDK build, 9 slice end-to-end, 3 shell-free end-to-end, 3 lifecycle end-to-end; log in [docs/evidence/test-run.log](docs/evidence/test-run.log); the v0.1 run is [test-run-v0.1.log](docs/evidence/test-run-v0.1.log)). The end-to-end tests drive the server through an MCP `ClientSession` over stdio, the same way an agent does.

## Limitations

* **Builds run trusted code.** `build` runs the project's own make or cmake files with your user's rights on the build host; there is no sandbox. Only build trees you trust.
* **Root on the target.** Programs run as root, because mkqnximage development images log in as root. Use disposable development targets.
* **setsid escapes.** A descendant that calls `setsid()` leaves the program's process group and is not killed at the end of a run (`process_stop` may end it afterwards, since its executable is below `/tmp`; not tested with an escaped process).
* **Races that remain in the v0.2 tools** (see the threat model under Security model):
  * `process_stop`: the process can still exec or change between the last check and a signal. The path rules judge the path the process was started with and what it resolves to now, so a symlink replaced after launch, or a hard link to a system program placed below `/tmp`, can mislead them. The creation token is taken by `process_stop` itself, not when the agent last called `processes`. It signals one process, not its children.
  * `remote_binary`: the file can be replaced between the check and the start; the runner repeats the path rules, not a content check, so the SHA-256 in the run record describes the file at check time. Shells are recognised by name: a copy of a shell renamed and placed below `/tmp` passes.
  * `file_list`: an entry's metadata and its link target are read one after the other and can describe different versions of it.
  * Address: a stale lease is noticed only when ssh fails to connect, and the command is then retried once at a rediscovered address; images that share host keys (a copied target directory) cannot be told apart.
* **Unverified SDK libraries.** Frames inside shared libraries are resolved against the SDK's copies on the build host, not verified against the libraries on the image; a custom image with different builds can give wrong library frames (gdb's warnings are returned in `gdb_warnings`).
* **Current source, not preserved source.** Source lines in a backtrace are read from the files on the build host at the time of `get_crash`; if you edited them after building, the lines shown are the edited ones.
* **Lossy text decoding.** Program output, logs and gdb text are decoded as UTF-8 with replacement characters; byte counts and output cursors are exact, but binary output is not reproduced exactly in text fields.
* Unique core names: qnx-mcp moves each core into its run's directory right after dumper reports it. Images created by `target_create` (without `--secpol`) also run dumper with sequential names (`-n`). On other images (`core_naming: "fixed"`), a second crash of a program with the same name in the moment between the dump and the move could overwrite the first core; `get_crash` then reports `core_mismatch` (pid check) instead of a wrong backtrace.
* Background runs are detached from the ssh session that started them but do not survive a target reboot (`run_poll` then reports `lost`; this path is not tested).
* Only QEMU targets created by mkqnximage on a libvirt network are supported; real boards and aarch64 targets are not yet. Host-side disk use is limited by record and size caps, not by a quota.
* On the shared test box, 3 of 13 mkqnximage builds reported success but left no `output/disk-qemu`, at times when other agents were running mkqnximage builds; `target_create` now rebuilds once with `--clean` in that case. The cause is not known.

## How this was built

qnx-mcp was written by AI coding agents (Claude Opus 5.5 in Claude Code) working for our team on a build box with QNX SDP 8.0 and QEMU, and reviewed by a second model (GPT-6 Astra in Codex CLI) before each release; its findings and what we did about them are in docs/DESIGN.md section 10. Every result in this README comes from a command that ran against QNX OS 8.0, and the logs are in docs/evidence.

## Licence

Apache-2.0, see [LICENSE](LICENSE). The QNX SDP is not part of this project and has its own licence.
