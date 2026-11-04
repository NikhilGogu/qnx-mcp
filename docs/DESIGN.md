# qnx-mcp design

qnx-mcp is a Model Context Protocol (MCP) server that gives coding agents typed, bounded tools for QNX OS 8.0 work: create, start, stop and destroy QEMU targets, build with the QNX SDP toolchain, deploy and run programs (one-shot or in the background), move files, turn dumper core files into source-level backtraces, and read the system state (pidin, slog2). It runs on the build host (where QNX SDP 8.0 and QEMU live) and speaks MCP over stdio, so any MCP client can use it, locally or through `ssh -T`.

Status (4 Nov 2026): v0.3.0. 19 tools, tested against QNX 8.0 x86_64 QEMU targets through an MCP client session (README "Tests"), and connected to Claude Code and Codex (v0.1). The review findings on the stage one code, v0.1 and the v0.2 additions for agents without a shell on the target (`file_list`, `process_stop`, `remote_binary`) are in section 10; v0.2.1 addressed the v0.2 findings. v0.3.0 fixes two gaps found in the study of section 9: `process_stop` reads the executable from `/proc/<pid>/exefile`, so services started with a relative path can be stopped, and `remote_binary` refuses shells and system programs.

## 1. Skills and the server

QNX publishes Markdown agent skills ([qnx/qnx-agent-skills](https://github.com/qnx/qnx-agent-skills)) that teach an agent QNX facts and procedures and reach the target over plain SSH. With skills alone, the agent composes the shell commands, parses free text and recalls the QNX specifics itself. qnx-mcp does this work in its tools:

| Need | Skills + plain SSH | qnx-mcp |
|---|---|---|
| Run a program | agent writes an ssh command line; quoting and timeouts are its problem | `deploy_and_run(args=[...], timeout_s)`: argv array, deadline, process group killed at the end, stdout/stderr separate and capped |
| Did it crash? | parse `Memory fault (core dumped)` and `$?` | `outcome: crashed`, `signal_name`, exact core path from dumper, `core_ready_ms` |
| Which core? | guess `/data/var/dumper/<name>.core` | dumper's own answer (`DCMD_DUMPER_NOTIFYEVENT` pulse, `DCMD_DUMPER_GETPATH`), core moved into the run's directory |
| Is it the right core? | not checked | build ID, deployed path and pid checked against the run; `core_mismatch` otherwise |
| Backtrace | copy core, pick gdb, set sysroot, read text | `get_crash`: faulting frame, first user frame, locals, source, every thread |
| Services | `nohup`, then tail files by hand | `run_start` / `run_poll` (byte cursors) / `run_stop` |
| Which target? | whatever IP the agent typed | named targets, allowlist, per-target lock, pinned host key |

Skills tell the agent what to do; qnx-mcp runs it and returns structured results. The skill file [skills/qnx-mcp/SKILL.md](../skills/qnx-mcp/SKILL.md) says which qnx-mcp tool to use when.

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
* **Address.** The target's MAC from `local/options`; the newest unexpired lease for that MAC in libvirt's lease file, else `mkqnximage --getip` (arp). No port is probed, because sshd on the QNX 8.0 images (OpenSSH 10.4) penalises connections that never attempt authentication: with one port probe per tool call, a burst of calls made the target drop every connection from the host for about 15 s (slog2: `srclimit_penalise: 192.168.122.1/32: activating ipv4 penalty of 15.383 seconds for penalty: connections without attempting authentication`; `docs/evidence/v0.2-target-observations.txt`). When ssh or scp fails while setting up the connection (exit 255 with `ssh: connect to host`, a host key failure or `kex_exchange_identification`), so the remote command never started, the address is rediscovered (all live leases and `mkqnximage --getip`) and the command is retried once at a different address, which later commands of the same call then use. A session that broke after the command started is never retried. During boot every candidate address gets an authenticated `ssh true` attempt.
* **Host keys.** With `--sshd-pregen=yes` (mkqnximage's default when ssh-keygen exists; `target_create` passes it explicitly) mkqnximage generates the host keys on the build host in `local/misc_files/ssh_host_{ed25519,rsa}_key` and puts them on the image's data partition (`/data/var/ssh`, qnx6, persistent). They survive reboots of the same disk; tested by SSH with `StrictHostKeyChecking=yes` after `target_stop` and `target_start`. qnx-mcp writes them into the target's own known_hosts as `qnx-mcp-<name> <type> <key>` and connects with `HostKeyAlias=qnx-mcp-<name>`, `StrictHostKeyChecking=yes`, `CheckHostIP=no`, `UpdateHostKeys=no`. The image's key files are the source of truth: if the pin file differs (for example a known_hosts written by an older version), it is rewritten from them. A target without pre-generated keys is refused (`host_key_missing`); `QNX_MCP_TRUST_FIRST_USE=1` instead pins whatever key answers first (weaker, reported as `host_key_policy: first_use`; untested against a real image).
* **QEMU process.** The pid in `output/qemu.pid` is opened as a pidfd first, then verified through `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`, then checked alive through the pidfd, so signals reach exactly the verified process. `target_stop` sends SIGTERM and then SIGKILL only through that pidfd (it does not call `mkqnximage --stop`, which signals a pid read from a file), waits on the pidfd for the exit and removes `output/qemu.pid`.
* **Create and cleanup.** `target_create` writes `local/snippets/post_start.custom` (unless `--secpol` is set) so the image restarts dumper with its own arguments plus `-n`, sequential core names. A black-box test on a running image showed `sh.1.core`, `sh.2.core`, `sh.3.core` for three crashes. A failed build or boot stops QEMU and removes the directory and state, keeping the mkqnximage log. On the shared test box, 3 of 13 builds ended with mkqnximage reporting success while `output/disk-qemu` was missing (other agents were running mkqnximage builds at the time; the cause is not known), so `target_create` rebuilds once with `--clean` when the disk image is missing.

### The target runner (qnx-mcp-run)

A C program (`src/qnx_mcp/runner/qnx-mcp-run.c`) built with qcc on first use (cached by source hash), uploaded and verified like any binary. Every mode except the raw copy mode prints exactly one JSON object; the host parses the whole output as one document and rejects a report that exceeded its byte budget.

* **One-shot run.** Forks the program into its own process group (both sides call `setpgid`), stdin `/dev/null`, stdout and stderr through pipes capped at `-m` bytes each (totals and truncation reported). The deadline is a POSIX timer whose signal handler kills the group, so it holds even if the main loop is blocked; SIGTERM, SIGHUP and SIGINT do the same. The main loop polls the output pipes, the exec-status pipe (a child that blocks before `exec` still meets the deadline) and, with `-S`, its own stdin: when the ssh client goes away the runner kills the program (`disconnected: true`). The program's exit is seen with `waitid(WNOWAIT)`: the zombie keeps the pid and the group id reserved while the runner kills the rest of the group and scans `/proc` with `getpgid()` until no member is left (`group_clean`; on QNX 8.0 `kill(-pgid, 0)` still succeeds while only the zombie leader is left, so it cannot be used for this), waits for the core, and only then reaps. No signal is sent to the pid or group after the reap. Output pipes still held by an escaped process are closed after 300 ms (`capture_complete: false`). Failed system calls are reported in `warnings[]`. With `-R dir` the runner holds a lock on `dir/active` for the whole run.
* **Crash path.** Before the fork the runner creates a channel and registers an updateable pulse with `MsgRegisterEvent` and `DCMD_DUMPER_NOTIFYEVENT` on `/proc/dumper`. After a dump-producing signal it waits for the pulse carrying the child's pid (`TimerTimeout` + `MsgReceivePulse`, bounded by `core_wait_s`; the zombie makes sure that pid is still the program's), records `core_ready_ms`, calls `DCMD_DUMPER_GETPATH` and renames the core into `/tmp/qnx-mcp/runs/<run_id>/` (same qnx6 file system, so the rename is atomic). Every call to dumper (open and registration, GETPATH, REMOVEEVENT) runs in a helper thread and the runner waits at most 3 s for it, because a server may keep a client reply-blocked even after a timeout; each call is also bounded with `TimerTimeout` and skipped if that cannot be set up. A pulse whose GETPATH then fails is reported as `notified_no_path` with the error, which separates "dumper no longer knows the path" from "no dump happened" (`timeout`).
* **Background run** (`-B dir`). The runner daemonizes (own session, standard streams on `/dev/null`), writes the program's stdout and stderr to `dir/stdout` and `dir/stderr` (capped), holds an fcntl write lock on `dir/runner.pid` while alive and writes `dir/status.json` atomically at the end. The launching process waits up to 10 s for the daemon's report; without it, it kills the daemon (still its unreaped child) and the program's group and reports `started: false`. `-P dir` (poll) returns the status, liveness from the lock (a pid could be reused) and output chunks from byte offsets. `-K dir` (stop) writes `stop` into the run's control FIFO `dir/ctl`, which only the owning runner reads; it never signals a pid. If the runner does not finish within the grace period, `acknowledged: false` is returned and the run's own deadline still applies.
* **Maintenance and files.** `-H file` (size, mode, SHA-256), `-X file -L n` (copy at most n bytes, raw), `-G dir` (prune old run or app directories, never one whose `runner.pid` or `active` is locked, one younger than 5 or 10 minutes, or one the host names as in flight), `-D rel -r root` (check, create the parents of, or with `-I tmp [-n]` atomically install a file_put destination; parents are opened one by one with `O_NOFOLLOW` below the root, directories and symlinks are refused as destinations, `-x /tmp/qnx-mcp` refuses anything whose real path is inside qnx-mcp's own directory, and `-n` uses `linkat` so an existing file is never replaced).

* **Listing** (`-l dir -L max`). Reads the whole directory with `errno` checked after every `readdir` (a failure ends the listing with `complete: false` and `read_error`), keeps the first `max` names in byte order (bounded memory; at most 1000), counts all entries, and reports each kept entry with `lstat` (type, size, mode, mtime) and `readlinkat` for links, which are never followed. `iofdinfo()` cannot name an opened directory on these images (it returned ENOSYS for every directory tried: qnx6, `/proc/boot`, `/`, `/dev/shmem`), so `real_path` comes from `realpath()` and `real_path_verified` is true only if `stat` of it gives the device and inode of the opened directory. An `alarm` ends the runner after 30 s if a `stat` blocks in a resource manager.
* **Stopping a process qnx-mcp did not start.** Discovery: `-Y pid` opens `/proc/<pid>/ctl` and reports `DCMD_PROC_INFO`'s `start_time` (nanoseconds since the epoch; the creation token), `crit_proc`, the `DCMD_PROC_MAPDEBUG_BASE` path (the string pidin shows: without the leading `/` for an absolute start, for example `tmp/hub`, as typed for a relative one, for example `./statusd`) and `/proc/<pid>/exefile`. QNX documents `exefile` as "the path of the executable file used to run the process" (Programmer's Guide, Controlling processes via the /proc filesystem); black-box on tb it holds an absolute path, the start directory joined with the relative path for a relative start (`/data/var/tmp/qnx-mcp-test/rel/./hangrel` for `./hangrel`; `docs/evidence/v0.2-target-observations.txt`), and the unresolved link for a start through a symlink. The same ssh call runs pidin; its path must equal the `-Y` path and name the same file as `exefile` (equal, or its tail after a `/`). Stop: `-Z pid -T start_ns -q path -s SIG -w grace_ms [-f] -A root... -F dir...` opens `/proc/<pid>/ctl` read-write and refuses unless the start time is the token, the path equals pidin's, the process is not critical, `exefile` (read by name while the descriptor holds the pid) names the same file, and the policy above allows `exefile` and its `realpath` with the `-A` roots and `-F` directories. Right before each signal the start time, `crit_proc` and the executable path are read again through the same descriptor; if any changed, nothing more is sent. The signal goes through that descriptor (`DCMD_PROC_SIGNAL`, which needs it open for writing); `DCMD_PROC_INFO` is then polled every 20 ms until `_NTO_PF_TERMING` or `_NTO_PF_ZOMBIE` is set, for at most `grace_ms`, and with `-f` SIGKILL follows and is waited for up to 2 s. A failed query is reported as `state_error` (the tool returns an error that says which signals went out), never as an exit. Observed on tb: while the descriptor is open, a process that exits after SIGTERM keeps answering on it with `_NTO_PF_TERMING` set, and a program that calls `exec` gets a new start time (`test_process_stop`).
* **Program policy** (`-H file` and runs, with `-x dir -A root... -F dir... -N name...`). A program must be an executable regular file (`stat`, mode bits and `access(X_OK)`); the policy allows it only if, both as given (in normal form, `.` and `..` removed without following links) and by its `realpath`, it is outside the `-x` directory (qnx-mcp's own) and every `-F` directory, inside some `-A` root, and neither base name is an `-N` name. `remote_binary` passes `-x /tmp/qnx-mcp`, the roots from `QNX_MCP_REMOTE_BINARY_ROOTS` (default `/tmp` and `/data`), `/proc/boot`, `/bin`, `/sbin`, `/usr` and `/system` as `-F`, and shell names as `-N` (`sh`, `ksh`, `mksh`, `pdksh`, `bash`, `dash`, `ash`, `zsh`, `csh`, `tcsh`, `fish`, QNX's `esh`, `fesh` and `uesh`, `toybox`, `busybox`, `toysh`); the check runs in `-H` before the run and again right before the fork. `-H` also reports `real_path` and `executable`, and `-H` and `-X` open with `O_NONBLOCK`, so a FIFO is refused without waiting for a writer.

### Runs, uploads and records

* `deploy_and_run` and `run_start` first copy the host binary into the run's own record directory (`state/targets/<t>/runs/<run_id>/bin/`) and check that copy (ELF class, arch, QNX loader). Everything after works on the snapshot, so a rebuild of the original cannot change what was deployed or what `get_crash` reads.
* Uploads are content-addressed: `/tmp/qnx-mcp/apps/<sha256 prefix>/<name>`. One ssh round trip creates directories, prunes old apps and runs, and hashes the runner and the app if present; missing or mismatching files are copied to a unique `.part-<random>` name, chmod'ed, hashed on the target (size, mode, SHA-256 must match), then renamed. QNX refuses to overwrite an executable that is still in use (stage one saw `scp: ... Resource busy`); content addressing means a running binary is never rewritten.
* Every remote command's exit status is checked (`remote_failed` with stderr), including the status probe, pidin and slog2info.
* Run records (`record.json`, mode 0600) are written while holding the target's shared lock and a short records lock; run_poll and run_stop also read them inside the target lock. A background run's record is saved before anything is launched; a start whose acknowledgement is lost, times out or is cancelled is stopped through its control FIFO (with cancellation suspended for that bounded clean-up) and marked `failed`, and a start left `starting` by a killed server is reconciled the same way later. The newest 40 finished records of any kind are kept on the host; runs still in flight are never pruned. On the target the runner prunes (see `-G`).
* **file_put** snapshots the host file into the server state with the size cap enforced while copying, checks the destination with `-D` (creating missing parents without following symlinks), copies the snapshot to a unique temporary name next to the destination, verifies size, mode and SHA-256, and installs it with `-D ... -I` (no-replace unless `overwrite`). **file_get** hashes the remote file, streams exactly that many bytes with `-X` into a staging file in the server state (the cap holds during the transfer), verifies the hash, then installs it into the destination directory through a directory descriptor: a unique temporary name, then `link` (never replaces) or, with `overwrite`, `rename` over a regular file. The destination directory itself is never chmod'ed.

### Crash analysis

`get_crash` hashes the core on the target, refuses it above `max_core_mb`, streams exactly the hashed size into a unique directory, verifies SHA-256, and decompresses `.gz` cores with the same cap. For a run, the binary snapshot must still have the SHA-256 that was deployed. Provenance comes from the core itself: the executable path gdb reports (`Core was generated by ...`) must equal the content-addressed path the run deployed, the pid in the core's thread list must be the run's pid, and the GNU build ID read from the executable's own note in the core's memory (at the address gdb relocated `.note.gnu.build-id` to, read through the core's generic PT_LOAD headers) must not contradict the binary's. A core passed with `core_path` and `binary` needs a binary with a build ID and a matching build ID in the core. Anything missing or contradictory is `core_mismatch`. Then the SDP gdb (`ntox86_64-gdb`/`ntoaarch64-gdb`) runs in MI mode with `set sysroot $QNX_TARGET/<arch>`, the SDK library paths, and tokenised commands: thread list, `$_siginfo` fields (QNX cores expose `$_siginfo.__data.__fault.__addr`), `x/i $pc`, the faulting thread's frames and locals, then `-thread-select N` and `-stack-list-frames` for up to 64 threads. Required records that are missing, malformed or duplicated fail with `gdb_failed`; optional records (a frame's locals, a thread's stack) are used only when the selection and the listing are both present, well-formed and unique. The result separates `faulting_frame` (innermost frame of the faulting thread, possibly in a library) from `user_frame` (innermost frame in your sources). Source lines are read only for C/C++ files, and only inside `QNX_MCP_WORKSPACE` when it is set, because the paths come from the binary's DWARF data.

**Runs of programs already on the target** (`remote_binary`). There is no host snapshot: the record keeps the target path, its real path and the SHA-256 the runner computed before the run (information only; the file can change before it starts). `get_crash` needs `run_id` plus `binary`, the host path of the program's unstripped build (deploy_and_run and run_poll say so in `next_step`; without it the error names the run and the program). Provenance (`source: remote_run`): the core's executable must be the started path or its real path and the core's pid the run's pid; a build ID in the binary that differs from the core's is `core_mismatch`. Only a matching build ID gives `verified: true`. Without a build ID in the binary or the core, the backtrace is returned with `verified: false` and `evidence: "pid and path match; build ID not available (...)"`. Tested on tb with the crash demo installed by `file_put`: verified with a build ID; unverified with the same program linked with `--build-id=none`; a rebuilt binary refused.

### Cancellation and bounds on the host

Tool bodies run in worker threads. If the client cancels a request (the Python SDK sends `notifications/cancelled` when a caller abandons a call), the server kills every host process the call started; for a run that includes ssh, so the runner sees EOF on stdin and kills the program. Tested: a cancelled 120 s run left no process on the target 0.5 s later and the next run started at once. Host commands keep at most 8 MiB per stream by default (gdb 32 MiB), draining and counting the rest; output that arrived is kept even if a descendant holds the pipes, and such descendants are killed 2 s after the command exits. On shutdown or SIGTERM the server kills any child it started. Host-side disk use (state directory) is bounded by the record and core limits; there is no quota.

## 3. Tools

Implemented and tested (v0.1): `environment_check`, `target_list`, `target_create`, `target_start`, `target_stop`, `target_destroy`, `target_status`, `build`, `deploy_and_run`, `run_start`, `run_poll`, `run_stop`, `get_crash`, `file_put`, `file_get`, `processes`, `logs`. Added in v0.2 for agents without a shell on the target: `file_list`, `process_stop`, and `remote_binary` for `deploy_and_run` and `run_start` (with `get_crash(run_id, binary)` for their crashes). README "Tools" has a table and example outputs.

Annotations: read-only and idempotent for `target_list`, `target_status`, `get_crash`, `processes`, `logs`, `run_poll`, `environment_check`, `file_list`; destructive for `target_stop`, `target_destroy`, `build` (it overwrites outputs and runs project scripts), `deploy_and_run`, `run_start`, `run_stop`, `file_put`, `process_stop`; `openWorldHint` false everywhere.

Planned (v1.0): `target_snapshot`/`target_restore` (qcow2 overlay so every trial starts from the same disk), `board_add` for real hardware over SSH, aarch64 QEMU images, `deploy_libs`, `run_as` (non-root user), `debug_session` (pdebug plus gdb/MI), `process_detail`, `logs_follow`, optional `trace_capture`/`trace_summary` with trace-studio (one-way, optional dependency), `run_valgrind` on `--valgrind=yes` images.

## 4. Output and error contract

Every tool returns JSON as MCP `structuredContent` and the same JSON as text. Success has `ok: true`. Failure sets MCP `isError: true` and returns `{ok: false, error: {code, message, hint, details}}`. Every declared `outputSchema` is `{"type": "object", "anyOf": [<success schema>, <error schema>]}`, so error results conform too; the SDK still validates successes against the success model before sending them. Arguments that fail the input schema get the same structured error (`invalid_argument` with `details.errors[].field`), replacing the SDK's plain-text message. The offline tests validate real error results of every tool against the advertised schemas with jsonschema.

Codes: `invalid_argument`, `path_not_allowed`, `target_not_found`, `target_not_allowed`, `target_exists`, `target_down`, `target_busy`, `ssh_failed`, `host_key_mismatch`, `timeout`, `cancelled`, `sdk_missing`, `tool_missing`, `ssh_key_missing`, `mkqnximage_failed`, `build_failed`, `arch_mismatch`, `not_a_qnx_binary`, `deploy_failed`, `copy_failed`, `runner_failed`, `remote_failed`, `core_not_found`, `core_too_large`, `core_mismatch`, `gdb_failed`, `file_exists`, `file_not_found`, `file_too_large`, `process_not_found`, `process_not_allowed`, `internal_error`. `hint` names the next action. A crash is a successful observation (`outcome: crashed`).

## 5. Security model

The agent is treated as capable, fallible and possibly prompt-injected (it reads logs and source written by others). The server enforces the boundaries, and the descriptions explain them.

1. **Target isolation.** Names must fully match `[a-z][a-z0-9_-]{0,31}` and the allowlist `QNX_MCP_ALLOW_TARGETS` (comma-separated globs, matched against the whole name). An unset or empty allowlist refuses every target; `*` must be explicit. Targets outside it are invisible to `target_list` and refused by every tool. Symlinked target directories are refused. On the shared build box the allowlist `tb,tb[0-9]*` keeps the agent away from the reference target and other agents' targets.
2. **No shell tool.** Remote commands are fixed argument lists built by the server; agent arguments are quoted one by one (`shlex.quote`).
3. **Validated paths and options.** All validation uses `re.fullmatch`. Remote paths: `/[A-Za-z0-9._+/-]{1,255}` without `.` or `..` components (scp's legacy protocol lets the remote shell see them). `file_put` writes only below `/tmp` or `/data` and never into `/tmp/qnx-mcp`. mkqnximage options are an allowlist with value patterns, because mkqnximage writes values into option files it later sources. Host paths are resolved with `realpath`; with `QNX_MCP_WORKSPACE` set they must lie inside it. `file_get` refuses existing files (unless `overwrite`) and symlinks at the destination and renames a verified temporary file into place.
4. **Bounded execution.** Host subprocesses: own process group, timeout, capped capture, killed on cancellation. Target programs: deadline, capped output, process group killed at the end. Cores: size limit before download and after decompression. Files: `max_mb`.
5. **Locking.** Per-target `flock`, shared by all server processes on the host: exclusive for create, start, stop and destroy; shared for runs, status probes, pidin, slog2, file transfer and core download. A waiting call fails with `target_busy` after 120 s.
6. **SSH.** Key-only, `BatchMode=yes`, connect and keepalive timeouts, `IdentitiesOnly`, pinned host keys under `HostKeyAlias` with strict checking (section 2).
7. **Host side.** `build` runs the project's own make or cmake files, which is equivalent to running code from that tree: only point it at trusted trees. Sandboxing builds (for example bubblewrap) is not implemented.
8. **Target privileges.** Programs run as root because mkqnximage development images log in as root. Planned: `run_as` through the runner and `--secpol` images.
9. **Data.** Cores, logs, run records and binary snapshots stay in `$QNX_MCP_STATE_DIR` (directories 0700, files 0600). Nothing leaves the machine except tool results returned to the client.
10. **Directory listing (v0.2).** `file_list` is read-only and may list any directory, as `file_get` may read any file; the path is validated like every remote path (normalised, safe characters; `/` is allowed), entries are described with `lstat` and links are never followed, incomplete reads and an unverified `real_path` are reported, and the output is bounded (`max_entries` up to 1000, output budget, 30 s alarm). Refusals tested on tb: `..` and `.` components, a trailing `/`, a relative path, a file, a missing directory.
11. **Programs already on the target (v0.2, policy v0.3.0).** `remote_binary` is validated as a remote path and must be an executable regular file below a root of `QNX_MCP_REMOTE_BINARY_ROOTS` (default `/tmp` and `/data`; set but empty refuses everything), outside `/tmp/qnx-mcp`, `/proc/boot`, `/bin`, `/sbin`, `/usr` and `/system`, and not named like a shell. The host checks the path as given; the runner checks it as given and by real path before the run and right before the program starts (section 2). These rules keep `remote_binary` from starting a shell, a system program, the runner (whose modes `-D` install files, `-G` prunes and `-K` stops runs) or another run's files, as root and with the agent's arguments. Tested refusals: `/proc/boot/ksh` (a shell), `/system/bin/slay`, links in `/tmp` to both (refused by real path), the runner itself and a link to it, a file with mode 0644, a missing file, a `..` path, a directory; offline also `/data/home/sh` and a path outside the roots.
12. **Stopping processes (v0.2, executable from /proc in v0.3.0).** `process_stop` is the only tool that acts on a process qnx-mcp did not start. pid 1 is refused before contacting the target; then the discovery (creation token, `exefile`, pidin's path) is checked on the host (`exefile` absolute, normalised and naming the same file as pidin's path; a name other than procnto; a process that is not critical; an executable below `/tmp` or `/data` and outside `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system` and `/tmp/qnx-mcp/bin`), and the runner repeats the checks through the process's own `/proc` descriptor and re-reads the token, the critical flag and the executable right before each signal (section 2). Tested on tb: services started as `./hang` and as `qnx-mcp-test/.../hang` are stopped; refusals of pid 1, slogger2 (`/proc/boot`), `/system/bin/sleep`, `./sleep` started in `/system/bin`, a symlink in `/tmp` to `/system/bin/sleep` (refused by the resolved path), the runner of a background run, a pid that does not exist, and a wrong creation token; SIGTERM of a program started outside qnx-mcp; a program that ignores SIGTERM (`still_running`, then `kill_after_grace` sends SIGKILL); a program that execs `/system/bin/sleep` on SIGTERM (SIGKILL withheld, error with `signal_sent: true`).
13. **Scope of the v0.2 rules.** The path rules of items 10 to 12 guard against mistakes. They do not stop an agent that sets out to break them: `deploy_and_run` runs any program the agent builds, as root, and such a program can stop any process or change any file. The races that remain are in section 11.

## 6. Client setup

The server binary is `qnx-mcp` from the venv (`pip install -e .`). Environment variables are listed in the README. Tested clients (sanitized transcripts in `docs/evidence/`):

* **Claude Code 2.1.312**: `claude mcp add -s project qnx -- ssh -T <box> env "QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*'" QNX_MCP_TARGETS_DIR=... <venv>/bin/qnx-mcp`, then a headless `claude -p` session: the init event showed the server `connected` with 17 `mcp__qnx__*` tools, the model listed them and called `target_status` (26.7 s for the whole session).
* **Codex CLI 0.160.0**: `codex exec` with `-c mcp_servers.qnx.command="ssh"` and `-c mcp_servers.qnx.args=[...]`; the transcript shows an `mcp_tool_call` to `target_status` with structured content (18.3 s).
* **Cursor and VS Code**: config snippets in the README; untested.

## 7. Codelab outline

Audience: developers new to QNX who already use a coding agent. About 45 minutes after the licence and SDK installation, which are a separate prerequisite page. Tested on Ubuntu 22.04 with KVM.

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

1. 0:00 to 0:15: the problem: an agent on an embedded target has a plain SSH session, and the QNX expertise (dumper, pidin, slog2, gdb sysroots) sits with the human.
2. 0:15 to 0:40: QNX identity and build: `target_create` (about 20 s with KVM, sped up), `target_status`, `build`.
3. 0:40 to 1:25: `deploy_and_run`: `outcome: crashed`, core path from dumper; `get_crash`: NULL dereference at `crash.c:21`, locals, source line, provenance verified; a rebuilt binary refused with `core_mismatch`.
4. 1:25 to 2:00: the agent patches the code, rebuilds, reruns and checks the calibrated readings; `processes` shows thread states of a background service.
5. 2:00 to 2:25: results of the comparison (section 9).
6. 2:25 to 2:45: codelab and repository.

## 9. Measurement

The evaluation is in [eval/](../eval), copied from the qnx-mcp-eval repository at commit 83925d9: the protocol preregistered in [eval/PROTOCOL.md](../eval/PROTOCOL.md) with dated amendments, the tasks with their hidden oracles, reference fixes and ground truth, the harness, every summary, and every trial in `eval/results/raw.tar.xz`. Claude Code 2.1.312 on Amazon Bedrock drives the agent; each trial gets a fresh QNX OS 8.0 x86_64 QEMU target and a fresh workspace; an automatic oracle with unseen inputs decides success, and a blind judge (no arm, no transcript) decides whether the stated root cause is right.

* **Phases 1 and 2** (eight tasks each; arm A = QNX's agent skills plus a root shell over SSH, arm B = the same plus qnx-mcp): every trial that ran passed in both arms (phase 1: pilot 8/8 and an arm A screen 6/6; phase 2: arm A screens 16/16 twice, pilot Sonnet 28 of 32 trials and Haiku 6 of 32 before the budget guard stopped it). With SSH available, Sonnet used qnx-mcp mainly for crashes, and Haiku, with the MCP tools deferred behind tool search, did not load them.
* **Phase 3, no shell on the target** (README "Debugging without a target shell"): arm A against arm C = the same skills plus qnx-mcp 0.2.1, with the agent's ssh, scp and sftp refusing the target. 160 trials: oracle 40/40 in every arm and model; root cause Sonnet 39/40 (A) and 40/40 (C), Haiku 40/40 (A) and 39/40 (C); arm C needed about twice the turns and 61 percent (Sonnet) and 47 percent (Haiku) more cost per trial at the median. Two gaps found there are fixed in v0.3.0: `process_stop` refused services started with a relative path (23 of 80 arm C trials), and `remote_binary` could have started a shell or a system program (no agent did).
* **Limits.** Both arms are at the ceiling: every trial that ran passed the oracle. Arm C's isolation was a PATH wrapper plus transcript review; the agents ran as the same Unix user as the harness. One root-cause miss is a judge parse failure counted as a miss; the v2-04 oracle accepts a stack-size workaround. The tasks are our own small programs on QEMU, with one client and two models, and costs are list-price estimates.

## 10. Review findings

Review 03 covers the stage one code (items 1 to 9), review 05 the v0.1 code (items 1 to 9 again, and findings A to I) and review 12 the v0.2 additions. Each point has its status and, where a failure path can be provoked, the test that shows it (test names refer to `tests/`).

**Review 03 (stage one code)**

| Item | Point | Status |
|---|---|---|
| 1 | Expired leases | Fixed: `test_units::test_leases_ignore_expired_and_prefer_newest` |
| 1 | `--getip` fallback during boot | Fixed in `wait_ssh` (every 10 s); not provoked, the lease file always answered first |
| 1 | Image-provisioned keys, HostKeyAlias, strict checking | Fixed: `test_lifecycle_e2e::test_create_stop_start_destroy` (pins equal the image keys; strict SSH after a restart) |
| 2 | Empty allowlist permits everything | Fixed: `test_allowlist_fails_closed`, `test_unset_allowlist_refuses_everything` |
| 2 | Regexes accept a trailing newline | Fixed: `test_fullmatch_rejects_trailing_newline` |
| 2 | Symlinks bypass target boundaries | Fixed: `test_symlinked_target_is_refused`, `test_destroy_refuses_symlinked_target` |
| 2 | DWARF paths expose host files | Fixed: `test_workspace_confines_host_paths` |
| 2 | Private state directories | Fixed: `test_private_dir_mode_and_symlink_refusal` |
| 2 | Builds execute host code; sandbox builds | Open: documented (README Limitations); `QNX_MCP_WORKSPACE` confines paths only |
| 2 | Drop target privileges | Open: programs run as root |
| 3 | Exec pipe blocks before the deadline | Fixed (polled with the deadline); not provoked |
| 3 | Unchecked fork setup, setpgid, kill, waitpid | Fixed (results checked, `warnings[]`); failures not provoked |
| 3 | Descendants abandoned | Fixed: `test_slice_e2e::test_runner_contract` (forked child gone, `group_clean`) |
| 3 | setsid escapes | Open: not containable with process groups; documented |
| 3 | TimerTimeout unchecked, dumper calls unbounded | Fixed (see review 05 G); not provokable without a wedged dumper |
| 3 | Capture incomplete not reported | Fixed (`capture_complete`, `*_truncated`): `test_runner_contract` (truncation) |
| 3 | GETPATH eviction versus absent dump | Fixed (`notified_no_path` versus `timeout`); not provoked |
| 4 | Unbounded host output | Fixed: `test_run_bounds_output` |
| 4 | Unlimited core transfer and decompression | Transfer fixed (review 05 H); decompression cap in code; not provoked (dumper does not gzip by default) |
| 4 | Disk quotas | Open |
| 4 | Cancellation cleanup | Fixed: `test_cancel_kills_running_process`, `test_runner_contract` (cancelled run) |
| 5 | Signals to an old numeric pid | Fixed (pidfd, review 05 A) |
| 5 | Probes without locking | Fixed (shared lock); the race itself not provoked |
| 5 | Records saved outside the lock | Fixed; not provoked |
| 6 | Mutable input, unchecked rename, presence trusted | Fixed: `test_crash_slice` (snapshot survives a rebuild); upload mismatch not provoked |
| 7 | Reused core names | Fixed: per-run move plus `dumper -n` on created images, `test_create_stop_start_destroy` (two concurrent crashes) |
| 7 | Mutable host binaries, shared downloads | Fixed: `test_crash_slice` |
| 7 | Library identities against the image | Open: frames in libraries use the SDK's copies |
| 8 | Malformed MI as success, tokens | Fixed: `test_mi_parser_flags_malformed_untokened_and_duplicates` |
| 8 | Faulting frame versus callers, all threads | Fixed: `test_threads_crash` |
| 8 | Runner JSON split on Unicode separators | Fixed: `test_runner_contract` (U+2028 argument) |
| 9 | Error objects outside the declared schemas | Fixed: `test_error_results_match_declared_schemas` |
| 9 | Argument failures, annotations | Fixed: same test (`cpus=9`), `test_tools_have_descriptions_schemas_and_annotations` |
| 9 | Recovery hints across clients | Partly fixed: Claude Code and Codex connect and call tools (docs/evidence); error paths not exercised through them |

**Review 05 (v0.1 code), items 1 to 9**

| Item | Point | Status |
|---|---|---|
| 1 | Missing keys silently enable first-use trust | Fixed: refused unless `QNX_MCP_TRUST_FIRST_USE=1`, `test_first_use_trust_is_off_by_default` |
| 2 | file_put path bypasses | Fixed (finding B); sandboxing and privileges still open (review 03 item 2) |
| 3 | Runner blocking and process identity | Fixed (findings G and A) |
| 4 | Size checked before an unbounded transfer | Fixed (finding H) |
| 5 | `target_stop` bypasses the pidfd | Fixed (finding A) |
| 5 | Records read before locking | Fixed: run_poll and run_stop read the record inside the target lock; not provoked |
| 7 | No executable provenance | Fixed (finding F) |
| 8 | Malformed or duplicate optional MI records accepted | Fixed in `get_crash` (both records must be present, well-formed and unique); the parser is tested, the use in `get_crash` is not provoked |
| 9 | Wire conformity unverified | Fixed: the schema tests in `test_mcp_offline.py` |

**Review 05, findings A to I**

| Finding | Status |
|---|---|
| A. Wrong-process signalling | Fixed: the runner keeps the zombie until the group is empty and the core is in (`test_runner_contract`, `group_clean`); `run_stop` goes through the owning runner's control FIFO (`test_background_runs_and_inspection`, `acknowledged`); `target_stop` uses only the pidfd (`test_create_stop_start_destroy`). If `waitid` were unavailable the runner falls back to reaping first and then does not signal the group (`group_clean: false`) |
| B. File boundary bypasses | Fixed: `test_remote_paths_must_be_normalised`, `test_write_root_refuses_managed_dir`, `test_slice_e2e::test_file_boundaries` (directory destination, symlinked parent, `/data/var/tmp/qnx-mcp` alias). Partly: scp still writes the temporary file by path after the checked walk, so a parent replaced by a symlink in that moment would redirect the temporary file; the install step re-walks and refuses it |
| C. Unsafe downloads | Fixed: `test_install_never_replaces_or_follows`, `test_file_boundaries` (directory mode unchanged) |
| D. Unacknowledged background jobs | Fixed: `test_pruning_and_failed_starts` (lost acknowledgement, daemon stopped, record `failed`); the runner's own report timeout kills its daemon (not provoked) |
| E. Pruning live work | Fixed: `test_pruning_and_failed_starts` (prune with keep 0 and age 0 skipped a live background run and a one-shot run in progress) |
| F. False provenance | Fixed: `test_crash_slice` (rebuilt binary, binary without build ID, modified snapshot are refused; executable, pid and build ID all match for the real run) |
| G. Deadlines fail open | Fixed in the runner (timer-signal deadline, dumper calls in a bounded helper thread, calls skipped when `TimerTimeout` fails); not provokable in tests |
| H. Host cleanup and limits | Fixed: `test_run_keeps_output_when_a_descendant_holds_the_pipe`, `test_run_streams_with_exact_cap`, `test_file_boundaries` (growing file). Quotas open |
| I. Poll JSON overflow | Fixed: `test_poll_budget_for_escaped_output` (2 x 1 MiB of control characters in one poll); `max_kb` limited to 1024 |

**Review 12 (v0.2: `file_list`, `remote_binary`, `process_stop`, the single-lease address lookup)**

| Point | Status |
|---|---|
| 1. process_stop: exec and critical-state races before either signal | Partly fixed: start time, `crit_proc` and the executable are read again through the held descriptor right before each signal; an exec changes the start time, so it is caught (`test_process_stop`: SIGKILL withheld after an exec on SIGTERM). Open: the window between that check and the signal; no QNX mechanism to serialize them was found |
| 1. Pathname is not executable identity (replaced symlinks, hard links) | Open, documented as a limitation; the rules are against mistakes (item 13) |
| 1. PID reuse before opening | Fixed: creation token (start time) from discovery, verified after opening (`test_process_stop`: a wrong token is refused); a real reuse was not provoked |
| 1. Query errors taken as exit | Fixed: an error result that says which signals were sent (`test_units::test_stop_result_never_reports_an_unknown_state_as_success`); a failing query itself was not provoked |
| 1. Descendants survive | Documented (one process is signalled) |
| 2. file_list: `real_path` resolved apart from the opened directory | Fixed: `real_path_verified` by device and inode (`test_file_list`: true; the false case only in `test_list_result_reports_incomplete_and_unverified_listings`) |
| 2. file_list: `readdir` errors look complete | Fixed: `errno` checked, `complete: false` (same unit test; a failing `readdir` was not provoked) |
| 2. file_list: metadata and link target from different versions | Open, documented |
| 2. remote_binary: replacement between check and start | Open, documented; starting a protected copy would change the path the program runs from, which the restart use case needs |
| 2. remote_binary: hash fallback in crash provenance | Fixed: removed; only a build ID verifies, otherwise `verified: false` with `evidence` (`test_remote_binary_runs`) |
| 3. One lease suppresses fallback | Fixed: rediscovery and one retry after a connection-setup failure (`test_units::test_stale_lease_is_rediscovered_after_a_connection_failure`) |
| 3. Several leases still probe | Fixed: no port probes at all; candidates get authenticated attempts |
| 3. Cloned host keys defeat identification | Open, documented; mkqnximage generates keys per image |
| 4. The passing tests do not establish the guarantees | Agreed: the exec race and the token check are now provoked on tb; pid reuse, query errors and `readdir` errors are not |

## 11. Open problems

* On images not created by qnx-mcp (`core_naming: "fixed"`), a second crash of a program with the same name between dumper finishing a core and the runner moving it would overwrite it; `get_crash` then fails with `core_mismatch` (pid check).
* Backtrace source lines are read from the current files on the build host; no copy is taken at build time.
* Output is decoded as UTF-8 with replacement characters, so binary output is not reproduced exactly in text fields (byte counts and cursors are exact).
* Library frames are resolved against the SDK copies (see section 10).
* A descendant that calls `setsid()` escapes the process group and is not killed.
* The lease-file fast path assumes libvirt's default network; other setups fall back to `mkqnximage --getip`.
* Background runs do not survive a target reboot (`run_poll` then reports `lost`; not tested).
* The missing-disk-image failure of mkqnximage on the shared box (section 2) is worked around; its cause is not known.
* `process_stop`: the window between the last check and each signal is open; the path rules judge the start path and today's resolution (a replaced symlink or a hard link to a system program can mislead them); children of the process are not signalled; the creation token is taken at `process_stop`'s own discovery.
* For `remote_binary` runs, the SHA-256 is taken before the run and the runner re-checks only the path rules just before the start, so the bytes that ran are not established; crash provenance therefore relies on the build ID alone. Shells are recognised by name, so a renamed copy of a shell below `/tmp` passes; and `deploy_and_run` runs whatever the agent builds, as root.
* `file_list` follows a symlink given as the directory itself (`real_path`, verified by device and inode); an entry's metadata and link target can describe different versions of it.
