# qnx-mcp design

qnx-mcp is a Model Context Protocol (MCP) server that gives AI coding agents typed, bounded tools for QNX OS 8.0 work: create, start, stop and destroy QEMU targets, build with the QNX SDP toolchain, deploy and run programs (one-shot or in the background), move files, turn dumper core files into source-level backtraces, and read the system state (pidin, slog2). It runs on the build host (where QNX SDP 8.0 and QEMU live) and speaks MCP over stdio, so any MCP client can use it, locally or through `ssh -T`.

Status (3 Nov 2026): release candidate v0.1. 17 tools, tested against QNX 8.0 x86_64 QEMU targets through a real MCP client session (48 tests passed in 89.5 s, see README "Tests"), and connected to Claude Code and Codex.

## 1. Why a server and not only skills

QNX publishes Markdown agent skills ([qnx/qnx-agent-skills](https://github.com/qnx/qnx-agent-skills)) that teach an agent QNX facts and procedures and reach the target over plain SSH. Skills are knowledge; the agent still has to compose shell commands, parse free text and remember QNX specifics. qnx-mcp supplies the hands and eyes:

| Need | Skills + plain SSH | qnx-mcp |
|---|---|---|
| Run a program | agent writes an ssh command line; quoting and timeouts are its problem | `deploy_and_run(args=[...], timeout_s)`: argv array, deadline, process group killed at the end, stdout/stderr separate and capped |
| Did it crash? | parse `Memory fault (core dumped)` and `$?` | `outcome: crashed`, `signal_name`, exact core path from dumper, `core_ready_ms` |
| Which core? | guess `/data/var/dumper/<name>.core` | dumper's own answer (`DCMD_DUMPER_NOTIFYEVENT` pulse, `DCMD_DUMPER_GETPATH`), core moved into the run's directory |
| Is it the right core? | hope | build ID, deployed path and pid checked against the run; `core_mismatch` otherwise |
| Backtrace | copy core, pick gdb, set sysroot, read text | `get_crash`: faulting frame, first user frame, locals, source, every thread |
| Services | `nohup`, then tail files by hand | `run_start` / `run_poll` (byte cursors) / `run_stop` |
| Which target? | whatever IP the agent typed | named targets, allowlist, per-target lock, pinned host key |

The two are complementary: skills tell the agent what to do, qnx-mcp lets it do it safely and see structured results. A short skill file ("when to use which qnx-mcp tool") is planned for the codelab.

## 2. Architecture

```
 agent (Claude Code, Codex, ...)                   build host (Ubuntu 22.04, QNX SDP 8.0, QEMU/KVM)
 +--------------+   MCP stdio (local or ssh -T)    +-----------------------------------------------+
 |  MCP client  | <------------------------------> | qnx-mcp (Python, mcp SDK 2.3, MCPServer)      |
 +--------------+                                  |  server: tools, schemas, cancellation         |
                                                   |  util:   bounded run(), flock, validation     |
                                                   |  sdk:    qnxsdp-env.sh, qcc/make/cmake, ELF   |
                                                   |  target: mkqnximage dirs, pidfd, ssh/scp      |
                                                   |  ops:    runs, files, pidin, slog2            |
                                                   |  crash:  core provenance, gdb/MI              |
                                                   +--------+-------------------------+------------+
                                              mkqnximage    |                         | ssh/scp (key, pinned host key)
                                                   +--------v-------------------------v------------+
                                                   | QNX OS 8.0 QEMU target (virbr0, DHCP)         |
                                                   |  /tmp/qnx-mcp/bin/qnx-mcp-run-<hash>  (C)     |
                                                   |  /tmp/qnx-mcp/apps/<sha256-16>/<name>         |
                                                   |  /tmp/qnx-mcp/runs/<run_id>/ (cores, output)  |
                                                   |  dumper, slogger2, pidin                      |
                                                   +-----------------------------------------------+
```

### Targets and identity

* A target is an mkqnximage directory `$QNX_MCP_TARGETS_DIR/<name>`. The server drives only the documented mkqnximage CLI (`--type=qemu --arch --ssh-ident --sshd-pregen=yes --ram --cpu --noprompt --run=-h`, `--stop`, `--getip`, `--clean`). Server state (locks, pinned keys, run records, mkqnximage log) lives in `$QNX_MCP_STATE_DIR/targets/<name>`, outside the image directory, because mkqnximage refuses to build into a non-empty directory. A target directory that is a symlink or resolves outside the targets dir is refused.
* **Address.** The target's MAC from `local/options`, the newest unexpired lease for that MAC in libvirt's lease file whose port 22 answers, else `mkqnximage --getip` (arp). During boot the arp fallback runs every 10 s. An address is never trusted as identity.
* **Host keys.** With `--sshd-pregen=yes` (mkqnximage's default when ssh-keygen exists; `target_create` passes it explicitly) mkqnximage generates the host keys on the build host in `local/misc_files/ssh_host_{ed25519,rsa}_key` and puts them on the image's data partition (`/data/var/ssh`, qnx6, persistent). They survive reboots of the same disk; tested by SSH with `StrictHostKeyChecking=yes` after `target_stop` and `target_start`. qnx-mcp writes them into the target's own known_hosts as `qnx-mcp-<name> <type> <key>` and connects with `HostKeyAlias=qnx-mcp-<name>`, `StrictHostKeyChecking=yes`, `CheckHostIP=no`, `UpdateHostKeys=no`. The image's key files are the source of truth: if the pin file differs (for example a known_hosts written by an older version), it is rewritten from them. Images without pre-generated keys get trust on first use under the same alias, kept until `target_create` or `target_destroy` (this path is not tested).
* **QEMU process.** The pid in `output/qemu.pid` is opened as a pidfd first, then verified through `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`, then checked alive through the pidfd, so signals reach exactly the verified process. `target_stop` runs `mkqnximage --stop`, then SIGTERM and SIGKILL through the pidfd, waiting on the pidfd for the exit.
* **Create and cleanup.** `target_create` writes `local/snippets/post_start.custom` (unless `--secpol` is set) so the image restarts dumper with its own arguments plus `-n`, sequential core names. A black-box test on a running image showed `sh.1.core`, `sh.2.core`, `sh.3.core` for three crashes. A failed build or boot stops QEMU and removes the directory and state, keeping the mkqnximage log. On the shared test box, 3 of 13 builds ended with mkqnximage reporting success while `output/disk-qemu` was missing (other agents were running mkqnximage builds at the time; the cause is not known), so `target_create` rebuilds once with `--clean` when the disk image is missing.

### The target runner (qnx-mcp-run)

A C program (`src/qnx_mcp/runner/qnx-mcp-run.c`) built with qcc on first use (cached by source hash), uploaded and verified like any binary. Every mode prints exactly one JSON object; the host parses the whole output as one document.

* **One-shot run.** Forks the program into its own process group (both sides call `setpgid`), stdin `/dev/null`, stdout and stderr through pipes capped at `-m` bytes each (totals and truncation reported). The main loop polls the output pipes, the exec-status pipe (so a child that blocks before `exec` still meets the deadline) and, with `-S`, its own stdin: when the ssh client goes away the runner kills the program (`disconnected: true`). At the deadline or on SIGTERM/SIGHUP/SIGINT it kills the group. When the program exits, the runner detects it with `waitid(WNOWAIT)`, kills the rest of the group while the zombie still holds the group id, reaps, and waits up to 2 s for the group to be empty (`group_clean`). Output pipes still held after that by an escaped process are closed after 300 ms (`capture_complete: false`). Failed system calls are reported in `warnings[]`.
* **Crash path.** Before the fork the runner creates a channel, registers an updateable pulse with `MsgRegisterEvent` and `DCMD_DUMPER_NOTIFYEVENT` on `/proc/dumper`. After a dump-producing signal it waits for the pulse carrying the child's pid (`TimerTimeout` + `MsgReceivePulse`, bounded by `core_wait_s`), records `core_ready_ms`, calls `DCMD_DUMPER_GETPATH` (bounded by `TimerTimeout` on send and reply, as are the registration calls), and renames the core into `/tmp/qnx-mcp/runs/<run_id>/` (same qnx6 file system, so the rename is atomic). A pulse whose GETPATH then fails is reported as `notified_no_path` with the error, which separates "dumper no longer knows the path" (for example its list of recent dumps was overrun) from "no dump happened" (`timeout`).
* **Background run** (`-B dir`). The runner daemonizes (own session, standard streams on `/dev/null`), writes the program's stdout and stderr to `dir/stdout` and `dir/stderr` (capped), holds an fcntl write lock on `dir/runner.pid` while alive and writes `dir/status.json` atomically at the end. `-P dir` (poll) returns the status, liveness from the lock (not from a pid that could be reused) and output chunks from byte offsets. `-K dir` (stop) checks that the lock holder is the recorded runner, sends SIGTERM, and after the grace period kills the program's group and the runner.
* **Hashing** (`-H file`): size, mode and SHA-256, used to verify every upload and download.

### Runs, uploads and records

* `deploy_and_run` and `run_start` first copy the host binary into the run's own record directory (`state/targets/<t>/runs/<run_id>/bin/`) and check that copy (ELF class, arch, QNX loader). Everything after works on the snapshot, so a rebuild of the original cannot change what was deployed or what `get_crash` reads.
* Uploads are content-addressed: `/tmp/qnx-mcp/apps/<sha256 prefix>/<name>`. One ssh round trip creates directories, prunes old apps and runs, and hashes the runner and the app if present; missing or mismatching files are copied to a unique `.part-<random>` name, chmod'ed, hashed on the target (size, mode, SHA-256 must match), then renamed. QNX refuses to overwrite an executable that is still in use (stage one saw `scp: ... Resource busy`); content addressing means a running binary is never rewritten.
* Every remote command's exit status is checked (`remote_failed` with stderr), including the status probe, pidin and slog2info.
* Run records (`record.json`, mode 0600) are written while holding the target's shared lock and a short records lock. The newest 40 records are kept on the host and the newest 20 run directories on the target (active background runs are never pruned).

### Crash analysis

`get_crash` hashes the core on the target, refuses it above `max_core_mb`, downloads it to a unique directory, verifies size and SHA-256, and decompresses `.gz` cores with the same cap. Provenance: the core's PT_NOTE segments must contain the binary's GNU build ID or, for qnx-mcp runs, the content-addressed path the run used (which embeds the binary's SHA-256 prefix); for a run, the pid in the core (from gdb's thread list) must be the run's pid. Otherwise `core_mismatch`. Then the SDP gdb (`ntox86_64-gdb`/`ntoaarch64-gdb`) runs in MI mode with `set sysroot $QNX_TARGET/<arch>`, the SDK library paths, and tokenised commands: thread list, `$_siginfo` fields (QNX cores expose `$_siginfo.__data.__fault.__addr`), `x/i $pc`, the faulting thread's frames and locals, then `-thread-select N` and `-stack-list-frames` for up to 64 threads. Required records that are missing, malformed or duplicated fail with `gdb_failed`; a thread's frames are used only if its `-thread-select` succeeded. The result separates `faulting_frame` (innermost frame of the faulting thread, possibly in a library) from `user_frame` (innermost frame in your sources). Source lines are read only for C/C++ files, and only inside `QNX_MCP_WORKSPACE` when it is set, because the paths come from the binary's DWARF data.

### Cancellation and bounds on the host

Tool bodies run in worker threads. If the client cancels a request (the Python SDK sends `notifications/cancelled` when a caller abandons a call), the server kills every host process the call started; for a run that includes ssh, so the runner sees EOF on stdin and kills the program. Tested: a cancelled 120 s run left no process on the target 0.5 s later and the next run started at once. Host commands keep at most 8 MiB per stream by default (gdb 32 MiB), draining and counting the rest. On shutdown or SIGTERM the server kills any child it started.

## 3. Tools

Implemented and tested (v0.1): `environment_check`, `target_list`, `target_create`, `target_start`, `target_stop`, `target_destroy`, `target_status`, `build`, `deploy_and_run`, `run_start`, `run_poll`, `run_stop`, `get_crash`, `file_put`, `file_get`, `processes`, `logs`. README "Tools" has a table and real outputs.

Annotations: read-only and idempotent for `target_list`, `target_status`, `get_crash`, `processes`, `logs`, `run_poll`, `environment_check`; destructive for `target_stop`, `target_destroy`, `build` (it overwrites outputs and runs project scripts), `deploy_and_run`, `run_start`, `run_stop`, `file_put`; `openWorldHint` false everywhere.

Planned (v1.0): `target_snapshot`/`target_restore` (qcow2 overlay so every trial starts from the same disk), `board_add` for real hardware over SSH, aarch64 QEMU images, `deploy_libs`, `run_as` (non-root user), `debug_session` (pdebug plus gdb/MI), `process_detail`, `logs_follow`, optional `trace_capture`/`trace_summary` with trace-studio (one-way, optional dependency), `run_valgrind` on `--valgrind=yes` images.

## 4. Output and error contract

Every tool returns JSON as MCP `structuredContent` and the same JSON as text. Success has `ok: true`. Failure sets MCP `isError: true` and returns `{ok: false, error: {code, message, hint, details}}`. Every declared `outputSchema` is `{"type": "object", "anyOf": [<success schema>, <error schema>]}`, so error results conform too; the SDK still validates successes against the success model before sending them. Arguments that fail the input schema get the same structured error (`invalid_argument` with `details.errors[].field`) instead of the SDK's plain text. The offline tests validate real error results of every tool against the advertised schemas with jsonschema.

Codes: `invalid_argument`, `path_not_allowed`, `target_not_found`, `target_not_allowed`, `target_exists`, `target_down`, `target_busy`, `ssh_failed`, `host_key_mismatch`, `timeout`, `cancelled`, `sdk_missing`, `tool_missing`, `ssh_key_missing`, `mkqnximage_failed`, `build_failed`, `arch_mismatch`, `not_a_qnx_binary`, `deploy_failed`, `copy_failed`, `runner_failed`, `remote_failed`, `core_not_found`, `core_too_large`, `core_mismatch`, `gdb_failed`, `file_exists`, `file_not_found`, `file_too_large`, `internal_error`. `hint` names the next action. A crash is a successful observation (`outcome: crashed`), not an error.

## 5. Security model

The agent is treated as capable but fallible and possibly prompt-injected (it reads logs and source written by others). The server enforces the boundaries; descriptions only explain them.

1. **Target isolation.** Names must fully match `[a-z][a-z0-9_-]{0,31}` and the allowlist `QNX_MCP_ALLOW_TARGETS` (comma-separated globs, matched against the whole name). An unset or empty allowlist refuses every target; `*` must be explicit. Targets outside it are invisible to `target_list` and refused by every tool. Symlinked target directories are refused. On the shared build box the allowlist `tb,tb[0-9]*` keeps the agent away from the reference target and other agents' targets.
2. **No shell tool.** Remote commands are fixed argument lists built by the server; agent arguments are quoted one by one (`shlex.quote`).
3. **Validated paths and options.** All validation uses `re.fullmatch`. Remote paths: `/[A-Za-z0-9._+/-]{1,255}` without `.` or `..` components (scp's legacy protocol lets the remote shell see them). `file_put` writes only below `/tmp` or `/data` and never into `/tmp/qnx-mcp`. mkqnximage options are an allowlist with value patterns, because mkqnximage writes values into option files it later sources. Host paths are resolved with `realpath`; with `QNX_MCP_WORKSPACE` set they must lie inside it. `file_get` refuses existing files (unless `overwrite`) and symlinks at the destination and renames a verified temporary file into place.
4. **Bounded execution.** Host subprocesses: own process group, timeout, capped capture, killed on cancellation. Target programs: deadline, capped output, process group killed at the end. Cores: size limit before download and after decompression. Files: `max_mb`.
5. **Locking.** Per-target `flock`, shared by all server processes on the host: exclusive for create, start, stop and destroy; shared for runs, status probes, pidin, slog2, file transfer and core download. A waiting call fails with `target_busy` after 120 s.
6. **SSH.** Key-only, `BatchMode=yes`, connect and keepalive timeouts, `IdentitiesOnly`, pinned host keys under `HostKeyAlias` with strict checking (section 2).
7. **Host side.** `build` runs the project's own make or cmake files, which is equivalent to running code from that tree: only point it at trusted trees. Sandboxing builds (for example bubblewrap) is not implemented.
8. **Target privileges.** Programs run as root because mkqnximage development images log in as root. Planned: `run_as` through the runner and `--secpol` images.
9. **Data.** Cores, logs, run records and binary snapshots stay in `$QNX_MCP_STATE_DIR` (directories 0700, files 0600). Nothing leaves the machine except tool results returned to the client.

## 6. Client setup

The server binary is `qnx-mcp` from the venv (`pip install -e .`). Environment variables are listed in the README. Tested clients (sanitized transcripts in `docs/evidence/`):

* **Claude Code 2.1.312**: `claude mcp add -s project qnx -- ssh -T <box> env "QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*'" QNX_MCP_TARGETS_DIR=... <venv>/bin/qnx-mcp`, then a headless `claude -p` session: the init event showed the server `connected` with 17 `mcp__qnx__*` tools, the model listed them and called `target_status` (26.7 s for the whole session).
* **Codex CLI 0.160.0** (profile astra): `codex exec` with `-c mcp_servers.qnx.command="ssh"` and `-c mcp_servers.qnx.args=[...]`; the transcript shows an `mcp_tool_call` to `target_status` with structured content (18.3 s).
* **Cursor and VS Code**: config snippets in the README, not tested.

## 7. Codelab outline: "Give your AI coding agent eyes and hands on a QNX target"

Audience: developers new to QNX who already use an AI coding agent. About 45 minutes after the licence and SDK installation, which are a separate prerequisite page. Tested on Ubuntu 22.04 with KVM.

1. **What you will build** (2 min): an agent that boots a QNX 8.0 VM, builds and runs a C program, diagnoses its crash from the core file and verifies the fix.
2. **Prerequisites** (separate page): QNX SDP 8.0 non-commercial licence through QNX Everywhere, `qnxsdp-env.sh`, QEMU with KVM, an ed25519 key. Install qnx-mcp in a venv; run `environment_check` and fix what it flags.
3. **Connect your agent** (5 min): one config block for Claude Code or Codex; ask the agent to list its qnx tools. Checkpoint.
4. **Boot a target** (5 min): `target_create`; what mkqnximage builds (IFS, partitions, sshd, dumper), why the target has its own directory, lock and pinned host key. Checkpoint.
5. **Build and read diagnostics** (5 min): break the code on purpose; errors as file/line/column objects.
6. **Crash, core, backtrace** (8 min): run `crash_demo`; `deploy_and_run` reports SIGSEGV and a core path; `get_crash` returns `read_calibration` at `crash.c:21` from `average_offset` and `main`, fault address 0x0, provenance verified. Side box: dumper, pulses, `DCMD_DUMPER_NOTIFYEVENT` and `DCMD_DUMPER_GETPATH`.
7. **Fix and validate behaviour** (5 min): handle the missing calibration table explicitly; rebuild, rerun, check the readings and the exit code.
8. **Look inside the system** (5 min): a background service with `run_start`/`run_poll`; `processes` thread states (RECEIVE, REPLY, SEND, NANOSLEEP) and what they say about message passing; `logs`.
9. **Recover from a fault** (3 min): stop the target mid-task; read `target_down` and its hint; start it again.
10. **Safety and clean-up** (2 min): a refused target outside the allowlist, an injection attempt passed verbatim, `target_destroy`.

## 8. Judge demo storyline (about 2:45)

1. 0:00 to 0:15: the problem. An agent on an embedded target today is a blind SSH session; QNX expertise (dumper, pidin, slog2, gdb sysroots) sits with the human.
2. 0:15 to 0:40: real QNX identity and build: `target_create` (about 20 s with KVM, sped up), `target_status`, `build`.
3. 0:40 to 1:25: `deploy_and_run`: `outcome: crashed`, core path from dumper; `get_crash`: NULL dereference at `crash.c:21`, locals, source line, provenance verified; a rebuilt binary refused with `core_mismatch`.
4. 1:25 to 2:00: the agent patches the code, rebuilds, reruns and checks the calibrated readings; `processes` shows thread states of a background service.
5. 2:00 to 2:25: measured results from the comparison (section 9), only once they exist.
6. 2:25 to 2:45: codelab, repository, and the disclosure that the code was written by AI agents.

## 9. Measuring the improvement over skills plus plain SSH

* **Tasks**: 8 seeded-bug tasks on QNX 8.0 QEMU, each with an automatic oracle: NULL dereference, use-after-free (abort), stack overflow, build error in a CMake project, wrong linker library, a hang (deadlock visible as MUTEX/CONDVAR states), a resource manager returning the wrong errno (visible in slog2), and a timeout-sensitive program.
* **Arms**: A = QNX agent skills + SSH; B = the same skills + SSH + qnx-mcp. Same model, prompt, image, privileges and budget; paired, randomised order; each trial starts from a reset disk and a fresh conversation.
* **Runs**: Claude Code headless and Codex exec, reported separately; smoke-test two tasks first, then preregister tasks, model versions, skills commit and ceilings.
* **Metrics**: completion (primary), correct root cause (blind-scored file:line), capped wall time, tokens, unsafe attempts versus actual effects, recovery after injected faults (tested separately).
* **Report**: per-task success counts, medians with ranges, every failure and its cost, sanitized transcripts. No claim about improvement is made before these runs exist.

## 10. Review disposition (GPT-6 Astra, answer 03)

Fixed: items 1 to 9 as described above (leases, getip fallback, pinned keys; fail-closed allowlist, fullmatch, symlinks, private state; runner deadline on the exec pipe, checked calls, group cleanup, bounded dumper calls, truncation flags; bounded capture, core and decompression limits, cancellation; pidfd, shared status lock, records inside the lock; snapshots, verified uploads, unique temporary names, checked statuses; per-run cores, sequential dumper naming on created images, provenance checks, unique downloads; strict MI parsing with token checks, faulting versus user frame, all threads, whole-document runner JSON; success-or-error schemas, structured argument errors, corrected annotations).

Not done in v0.1, with reasons:
* Sandboxing builds: needs a per-host design (bubblewrap is not on every build host); documented instead, with `QNX_MCP_WORKSPACE` for path confinement.
* Dropping target privileges: needs a non-root account in the image and changes to the runner; planned as `run_as`.
* Verifying shared libraries in a core against the actual image: would need the image's own copies (a per-target sysroot fetched from the target); planned. gdb warnings are returned meanwhile.
* Transactional record updates: records are whole-file atomic replacements under a lock, which covers the races found; a journal was not needed.

## 11. Open problems

* On images not created by qnx-mcp (`core_naming: "fixed"`), a second crash of a program with the same name between dumper finishing a core and the runner moving it would overwrite it; `get_crash` then fails with `core_mismatch` (pid check) rather than analysing the wrong process.
* Library frames are resolved against the SDK copies (see section 10).
* A descendant that calls `setsid()` escapes the process group and is not killed.
* The lease-file fast path assumes libvirt's default network; other setups fall back to `mkqnximage --getip`.
* Background runs do not survive a target reboot (`run_poll` then reports `lost`; not tested).
* The missing-disk-image failure of mkqnximage on the shared box (section 2) is worked around, not explained.
