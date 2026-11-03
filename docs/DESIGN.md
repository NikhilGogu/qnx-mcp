# qnx-mcp design

qnx-mcp is a Model Context Protocol (MCP) server that gives AI coding agents typed, bounded tools for QNX OS 8.0 work: create and boot QEMU targets, build with the QNX SDP toolchain, deploy and run programs, turn dumper core files into source-level backtraces, and read the system state (pidin, slog2). It runs on the build host (where QNX SDP 8.0 and QEMU live) and speaks MCP over stdio, so any MCP client can use it, locally or through `ssh -T`.

Status (3 Nov 2026): the vertical slice below works against QNX 8.0 x86_64 QEMU targets and is covered by tests that run without an agent.

## 1. Why a server and not only skills

QNX already publishes Markdown agent skills ([qnx/qnx-agent-skills](https://github.com/qnx/qnx-agent-skills)) that teach an agent QNX facts and procedures and reach the target over plain SSH. Skills are knowledge; the agent still has to compose shell commands, parse free text and remember QNX specifics (where dumper puts cores, which gdb, which sysroot, how `pidin` columns line up). qnx-mcp supplies the hands and eyes:

| Need | Skills + plain SSH | qnx-mcp |
|---|---|---|
| Run a program | agent writes an ssh command line; quoting is its problem; no timeout unless it remembers one | `deploy_and_run(args=[...], timeout_s)`: argv array, quoted, group-killed on timeout, stdout/stderr separate |
| Did it crash? | parse `Memory fault (core dumped)` and `$?`=139 | `outcome: crashed`, `signal_name: SIGSEGV`, exact `core_path` from dumper |
| Where is the core? | guess `/data/var/dumper/<name>.core` (or `~/cores` for uid >= 1000, per `dumper -M 1000 -D cores`) | asked from dumper itself via `DCMD_DUMPER_NOTIFYEVENT` + `DCMD_DUMPER_GETPATH`, then kept per run |
| Backtrace | copy core, pick `ntox86_64-gdb`, set sysroot and solib path, read text | `get_crash`: frames with function, file, line, locals, source lines, fault address, faulting instruction, summary |
| Build errors | read compiler text | `diagnostics[]` objects (gcc JSON diagnostics when qcc is driven directly) |
| Which target? | whatever IP the agent typed | named targets, allowlist, per-target lock, pinned host key |

The two are complementary: skills tell the agent what to do (porting, packaging, platform facts), qnx-mcp lets it do it safely and see structured results. We will ship a short skill file ("when to use which qnx-mcp tool") so skills-capable agents pick the tools up naturally.

## 2. Architecture

```
 agent (Claude Code, Codex, Cursor, VS Code)       build host (Ubuntu 22.04, QNX SDP 8.0, QEMU/KVM)
 ┌──────────────┐   MCP stdio (local or ssh -T)   ┌──────────────────────────────────────────────┐
 │  MCP client  │ ◄─────────────────────────────► │ qnx-mcp (Python, mcp SDK 2.3, MCPServer)      │
 └──────────────┘                                  │  util: bounded run(), flock, validation       │
                                                   │  sdk: qnxsdp-env.sh, qcc/make/cmake, ELF     │
                                                   │  target: mkqnximage per-target dirs, ssh/scp │
                                                   │  ops: run, crash (gdb/MI), pidin, slog2      │
                                                   └───────┬───────────────────────────┬─────────┘
                                                 mkqnximage│ --run=-h, --stop           │ ssh/scp (key, BatchMode)
                                                   ┌───────▼───────────────────────────▼─────────┐
                                                   │ QNX OS 8.0 QEMU target (virbr0, DHCP)        │
                                                   │  /tmp/qnx-mcp/bin/qnx-mcp-run-<hash>  (C)    │
                                                   │  dumper -d /data/var/dumper, slogger2, pidin │
                                                   └──────────────────────────────────────────────┘
```

* **Targets** are mkqnximage directories (`$QNX_MCP_TARGETS_DIR/<name>`). The server only drives the documented mkqnximage CLI (`--type=qemu --arch --ssh-ident --ram --cpu --noprompt --run=-h`, `--stop`, `--getip`). Server state (lock, known_hosts, run records, mkqnximage log) lives outside the image directory in `$QNX_MCP_STATE_DIR/targets/<name>`, because mkqnximage refuses to build into a non-empty directory.
* **IP discovery**: the target's MAC from `local/options`, newest libvirt DHCP lease for that MAC, a TCP probe on port 22, and `mkqnximage --getip` (arp) as fallback. QEMU liveness is the pid in `output/qemu.pid` verified through `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`, so a recycled pid is never trusted.
* **qnx-mcp-run** is a small C program built with qcc on first use (cached by source hash) and copied to the target. It forks the program into its own process group with stdin on `/dev/null`, captures stdout and stderr through pipes with a byte cap, kills the process group at the deadline, and prints one JSON object. Before the fork it creates a channel, registers an updateable pulse with `MsgRegisterEvent` and `DCMD_DUMPER_NOTIFYEVENT` on `/proc/dumper`; after a dump-producing signal it waits (`TimerTimeout` + `MsgReceivePulse`) for the pulse carrying the child's pid and calls `DCMD_DUMPER_GETPATH`. This uses QNX native message passing and the dumper's client interface instead of guessing file names, and it also tells us when the core is complete.
* **Uploads** are content-addressed (`/tmp/qnx-mcp/apps/<sha256 prefix>/<name>`, newest 20 kept) and written as `.part` then renamed. QNX refuses to overwrite an executable that is still in use: in our two-agent test, scp failed with `Resource busy` when the second agent re-uploaded `/tmp/qnx-mcp/bin/hang` right after the first agent's run of it was killed. Identical binaries are no longer re-sent, and the program keeps its own file name, so dumper still names the core after it.
* **get_crash** copies the core to the host, runs the SDP gdb (`ntox86_64-gdb`/`ntoaarch64-gdb`) in MI mode with `set sysroot $QNX_TARGET/<arch>` and the SDK library paths, sends tokenised MI commands (`-thread-info`, `-stack-list-frames`, `-stack-list-variables`, `$_siginfo` fields) and parses MI records, not CLI text. QNX cores expose `$_siginfo.__data.__fault.__addr`, which gives the fault address (0x0 for a NULL dereference).

## 3. Tools

### In this slice (implemented and tested)

| Tool | What it does | Output highlights |
|---|---|---|
| `environment_check` | SDP env, qcc, mkqnximage, gdb, KVM, QEMU, ssh, key, lease file | checks[] with fix hints |
| `target_list` | allowed targets under the targets dir, no SSH | name, state, arch, mac |
| `target_create` | mkqnximage build + headless boot + wait for SSH; allowlisted extra options | status incl. uname, dumper_running |
| `target_start` / `target_stop` | boot without rebuild / mkqnximage --stop then verified SIGTERM/SIGKILL | state, ip, was_running |
| `target_status` | state, IP, SSH probe, target clock, dumper presence | `running` / `booting` / `stopped` |
| `build` | qcc, make or cmake (generated QNX toolchain file) with the SDP env | diagnostics[], artifacts[] (ELF arch, loader, debug info), steps[] |
| `deploy_and_run` | ELF checks, copy, run via qnx-mcp-run with timeout and caps | outcome, exit_code/signal, stdout, stderr, core_path, run_id |
| `get_crash` | core to host, gdb/MI, structured backtrace | frames[], crashing_frame, locals, source, fault_address, summary |
| `processes` | two `pidin -F` passes (process and thread formats) | per process: ids, args, sizes, thread states; optional threads with priority, policy, blocked_on |
| `logs` | `slog2info -f -s -t`, filtered by buffer, process or pid, time (target clock), severity, substring | entries with epoch and ISO time, pid, thread, severity |

### Planned (v1.0)

* **Targets**: `target_destroy` (mkqnximage --wipe), `target_snapshot`/`target_restore` (qcow2 overlay so every trial starts from the same disk), `target_reboot`, `board_add` (real hardware over SSH: host, user, key, arch; no lifecycle tools), aarch64 QEMU images.
* **Files and runs**: `file_put`/`file_get` with validated paths, `deploy_libs` (copy the shared objects a binary needs), `run_start`/`run_poll`/`run_stop` for long-running services with incremental output, `run_as` (non-root user through the runner).
* **Debugging**: `debug_session` (pdebug on the target plus gdb/MI on the host: breakpoints, step, inspect, all MI-structured), `core_list`, build-id check between host binary and core.
* **Observability**: `process_detail` (`pidin -p <pid>` fds, channels, timers, memory), `system_info` (`pidin info`, syspage), `logs_follow` (bounded `slog2info -W`), `resmgr_list` (pathname space and the resource managers behind it).
* **Tracing (optional trace-studio integration)**: `trace_capture` runs tracelogger for a bounded duration with class filters and returns the `.kev` path; `trace_summary` calls our trace-studio converter when it is installed and returns its analyses as JSON (per-thread CPU, IPC send/receive/reply latency, blocking chains, priority-inversion suspects, missed deadlines) plus a Perfetto file for humans. Without trace-studio the tool returns the `.kev` and basic event counts from traceprinter. The dependency is optional and one-way: qnx-mcp never requires trace-studio.
* **Memory**: `run_valgrind` on images built with `--valgrind=yes`, returning memcheck findings as objects.

## 4. Output and error contract

Every tool returns JSON as MCP `structuredContent` (with a declared `outputSchema`) and the same JSON as text for older clients. Success has `ok: true`. Failure sets MCP `isError: true` and returns `{ok: false, error: {code, message, hint, details}}`. Codes are stable: `invalid_argument`, `target_not_found`, `target_not_allowed`, `target_exists`, `target_down`, `target_busy`, `ssh_failed`, `host_key_mismatch`, `timeout`, `sdk_missing`, `tool_missing`, `ssh_key_missing`, `mkqnximage_failed`, `build_failed`, `arch_mismatch`, `not_a_qnx_binary`, `deploy_failed`, `copy_failed`, `runner_failed`, `core_not_found`, `gdb_failed`, `remote_failed`, `internal_error`. `hint` always names the next action (for example "Call target_start with name='tb2'.").

## 5. Security model

The agent is treated as capable but fallible and possibly prompt-injected (it reads logs and source written by others). The server enforces the boundaries; descriptions only explain them.

1. **Target isolation.** Names must match `^[a-z][a-z0-9_-]{0,31}$` and the allowlist `QNX_MCP_ALLOW_TARGETS` (comma-separated globs). Targets outside it are invisible to `target_list` and refused by every tool (`target_not_allowed`). On our shared build box the allowlist `tb,tb[0-9]*` keeps the agent away from other people's targets and the reference target.
2. **No shell tool.** There is no "run this command" tool. Remote commands are fixed argv lists built by the server; agent-supplied arguments are quoted element by element (`shlex.quote`) for the QNX shell. A test passes `$(touch ...)`, `; touch ...`, backticks, quotes and `*` and checks the program receives them verbatim.
3. **Validated paths and options.** Remote paths must match `^/[A-Za-z0-9._+/-]+$` without `..` (scp's legacy protocol lets the remote shell see them). mkqnximage options are an allowlist with value patterns, because mkqnximage writes values into option files it later sources.
4. **Bounded execution.** Every host subprocess runs in its own session with a timeout and is killed as a process group; target programs are killed as a process group by qnx-mcp-run; output is capped (default 64 KiB per stream). Long operations have explicit limits (`boot_timeout_s`, `timeout_s`, `core_wait_s`).
5. **Locking.** Per-target `flock` files: exclusive for lifecycle and runs, shared for reads. Locks work across server processes, so two agents on one host are serialised (tested), and a waiting call fails with `target_busy` after 120 s instead of hanging.
6. **SSH.** Key-only, `BatchMode=yes`, connect and keepalive timeouts, `IdentitiesOnly`. Each target has its own known_hosts file with `StrictHostKeyChecking=accept-new`; it is reset when the server builds or boots the target, so if another VM later answers on that IP the call fails with `host_key_mismatch` instead of running code on the wrong machine.
7. **Host side.** `build` runs the project's own make/cmake, which is equivalent to running code from that tree; point it only at trusted trees. Planned: `QNX_MCP_WORKSPACE` to confine host paths, and an optional bubblewrap sandbox for builds.
8. **Target privileges.** The slice runs programs as root because mkqnximage development images log in as root. Planned: run as `qnxuser` through the runner, and images with `--secpol` policies; for real boards, a dedicated account with a forced command for the runner.
9. **Data.** Cores, logs and run records stay in `$QNX_MCP_STATE_DIR` on the build host; nothing leaves the machine except tool results returned to the client. Planned: an audit log (JSON lines) of every call.

## 6. Client setup

The server binary is `qnx-mcp` from the venv (`pip install -e .`). Environment: `QNX_MCP_SDP_ENV` (default `~/qnx800/qnxsdp-env.sh`), `QNX_MCP_TARGETS_DIR` (default `~/.qnx-mcp/targets`), `QNX_MCP_STATE_DIR`, `QNX_MCP_ALLOW_TARGETS` (default `*`), `QNX_MCP_SSH_KEY` (default `~/.ssh/id_ed25519`), `QNX_MCP_DHCP_LEASES`. Target creation takes 20 s to 3 min, so raise client tool timeouts where they exist.

**Claude Code** (project `.mcp.json`, or `claude mcp add qnx -e QNX_MCP_ALLOW_TARGETS='dev*' -- /path/to/venv/bin/qnx-mcp`):
```json
{ "mcpServers": { "qnx": { "command": "/home/me/venv/bin/qnx-mcp",
    "env": { "QNX_MCP_SDP_ENV": "/home/me/qnx800/qnxsdp-env.sh", "QNX_MCP_ALLOW_TARGETS": "dev*" } } } }
```

**Codex CLI** (`codex mcp add qnx --env QNX_MCP_ALLOW_TARGETS='dev*' -- /path/to/venv/bin/qnx-mcp`, or `~/.codex/config.toml`):
```toml
[mcp_servers.qnx]
command = "/home/me/venv/bin/qnx-mcp"
env = { QNX_MCP_SDP_ENV = "/home/me/qnx800/qnxsdp-env.sh", QNX_MCP_ALLOW_TARGETS = "dev*" }
tool_timeout_sec = 600
```

**Cursor** (`.cursor/mcp.json`): same shape as Claude Code's `mcpServers` block.

**VS Code** (`.vscode/mcp.json`, agent mode):
```json
{ "servers": { "qnx": { "type": "stdio", "command": "/home/me/venv/bin/qnx-mcp",
    "env": { "QNX_MCP_ALLOW_TARGETS": "dev*" } } } }
```

**Agent on a laptop, SDK and QEMU on a build box**: use ssh as the command, for example `"command": "ssh", "args": ["-T", "buildbox", "/home/me/venv/bin/qnx-mcp"]`. Verified with a raw JSON-RPC client on the laptop: initialize (protocol 2025-06-18), tools/list, `target_status` on the box's target, and a refused `target_stop` on a target outside the allowlist, in 1.1 s total.

The exact config keys for Codex, Cursor and VS Code will be re-checked against each client's current documentation during the codelab dry run.

## 7. Codelab outline: "Give your AI coding agent eyes and hands on a QNX target"

Audience: developers new to QNX who already use an AI coding agent. About 45 minutes. Tested on Ubuntu 22.04 with KVM.

1. **What you will build** (2 min): an agent that boots a QNX 8.0 VM, builds and runs a C program, diagnoses its crash from the core file and verifies the fix.
2. **Prerequisites** (10 min): QNX SDP 8.0 non-commercial licence through QNX Everywhere, `qnxsdp-env.sh`, QEMU with KVM, an ed25519 key. Install qnx-mcp in a venv; run the `environment_check` tool and fix what it flags.
3. **Connect your agent** (5 min): one config block for Claude Code, Codex, Cursor or VS Code; ask the agent to list its qnx tools.
4. **Boot a target** (5 min): the agent calls `target_create`; learn what mkqnximage builds (IFS, partitions, sshd, dumper) and why the target has its own directory and lock.
5. **Build and read diagnostics** (5 min): break the code on purpose; see errors as file/line/column objects instead of compiler text.
6. **Crash, core, backtrace** (8 min): run `crash_demo`; `deploy_and_run` reports SIGSEGV and a core path; `get_crash` returns `read_calibration` at `crash.c:21`, called from `average_offset` and `main`, fault address 0x0. Side box: how dumper works and how qnx-mcp-run uses `DCMD_DUMPER_NOTIFYEVENT` and `DCMD_DUMPER_GETPATH` (QNX message passing with pulses).
7. **Fix and rerun** (3 min): the agent adds the NULL check, rebuilds, reruns, sees `outcome: exited`, exit code 0.
8. **Look inside the system** (5 min): `processes` (thread states RECEIVE, REPLY, SEND and what they say about message passing), `logs` (slog2 buffers, filtering by process and time).
9. **Safety** (2 min): try to make the agent touch a target outside the allowlist or inject a shell command; see the structured refusal.
10. **Next** (1 min): real boards over SSH, kernel trace capture and trace-studio summaries.

## 8. Judge demo storyline (video under 3 minutes)

1. 0:00 to 0:15: the problem. An agent on an embedded target today is a blind SSH session; QNX expertise (dumper, pidin, slog2, gdb sysroots) sits with the human.
2. 0:15 to 0:40: Claude Code gets the task "the sensor service crashes on the target; find and fix the bug". It calls `target_create` (18 s with KVM, shown sped up) and `target_status`.
3. 0:40 to 1:10: `build` then `deploy_and_run`: `outcome: crashed`, SIGSEGV, core path from dumper in about 60 ms.
4. 1:10 to 1:40: `get_crash`: NULL dereference at `crash.c:21` in `read_calibration` called from `average_offset` and `main`, locals `s` and `index`, the source line. The agent explains the root cause (sensor without calibration table).
5. 1:40 to 2:05: the agent patches the code, rebuilds, reruns: `outcome: exited`, exit 0. `processes` and `logs` confirm the system is healthy.
6. 2:05 to 2:40: side-by-side numbers from the measured comparison (below): success rate, time, tool calls and tokens for skills plus SSH versus skills plus qnx-mcp.
7. 2:40 to 3:00: safety in ten seconds (allowlist refusal, injection test) and the codelab link.

### Measuring the improvement over skills plus plain SSH

* **Tasks**: 8 seeded-bug tasks on QNX 8.0 QEMU, each with an automatic oracle: NULL dereference, use-after-free (abort), stack overflow, build error in a CMake project, wrong linker library, a hang (deadlock between two threads, visible as MUTEX/CONDVAR states), a resource manager returning the wrong errno (visible in slog2), and a timeout-sensitive program.
* **Arms**: A = QNX agent skills + shell/SSH; B = the same skills + qnx-mcp. Same model, prompt, image and budget. Each trial starts from a fresh target (snapshot restore) so arms cannot leak state.
* **Runs**: Claude Code headless (`claude -p` with stream-json output) and Codex exec, 5 trials per task per arm.
* **Metrics**: fix success (oracle passes), correct root cause (agent's stated file:line versus ground truth), wall time, turns and tool calls, input and output tokens, unsafe actions (commands touching other targets, unbounded commands, destructive commands), and recovery after injected faults (target stopped mid-task, ssh timeout).
* **Report**: medians with interquartile ranges per task and arm, plus raw transcripts in the repo. No claim about improvement is made before these runs exist.

## 9. Open problems

* dumper as configured by mkqnximage reuses `<name>.core`; qnx-mcp moves each run's core to `/tmp/qnx-mcp/cores/<run_id>-<name>.core` right after the run (keeps 20). A crash of the same binary outside qnx-mcp between the run and the move could still overwrite it.
* Shared libraries in the core (for example `/proc/boot/libc.so.6`) are resolved against the SDK copies. A custom image with different library builds would need a per-target sysroot cache fetched from the target.
* Only the crashing thread gets a full backtrace; other threads get their top frame.
* Host keys are pinned per boot (accept-new after each start), weaker than pinning keys generated at image build time.
* Runs are one-shot with captured output; long-running services need `run_start`/`run_poll`.
* The lease-file fast path assumes libvirt's default network; other setups fall back to `mkqnximage --getip`.
