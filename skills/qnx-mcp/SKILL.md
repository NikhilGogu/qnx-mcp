---
name: qnx-mcp
description: "Which qnx-mcp tool (mcp__qnx__*) to use for QNX OS 8.0 work on QEMU targets: create, start, stop and destroy targets; build with the QNX SDP; run programs once or in the background, including programs already installed on the target; copy files and list target directories; stop processes qnx-mcp did not start; turn a crash into a source-level backtrace; diagnose hangs from thread states; read slog2 logs. Read this before building or running anything when the qnx MCP tools are available, and especially when you have no shell on the target. Written for qnx-mcp v0.2.1 (19 tools)."
license: Apache-2.0
---

# qnx-mcp: which tool for which job

The `qnx` MCP server runs on the build host, where the QNX SDP 8.0 and QEMU live. Every tool returns JSON. Success has `ok: true`; failure has `ok: false` and `error: {code, message, hint}`. Read the `hint`: it names the next action. A crash is not an error: `deploy_and_run` succeeds with `outcome: "crashed"`.

In Claude Code the tools are called `mcp__qnx__<tool>` and may be deferred; load the ones you need first (for example ToolSearch `select:mcp__qnx__build,mcp__qnx__deploy_and_run,mcp__qnx__get_crash`).

## Targets

| You want to | Use |
|---|---|
| Check the host before anything else (SDP, qcc, KVM, QEMU, SSH key, allowlist) | `environment_check()` |
| See which targets exist and whether they run (cheap, no SSH) | `target_list()` |
| Make a new QNX 8.0 QEMU target (mkqnximage build and boot, 20 s to 3 minutes) | `target_create(name, arch, ram, cpus, options, boot_timeout_s)` |
| Check one target: state, IP, uname, clock, dumper running and its arguments | `target_status(name)` |
| Boot an existing target again (no rebuild) | `target_start(name, boot_timeout_s)` |
| Power a target off (files on its disk stay; background runs are lost) | `target_stop(name)` |
| Delete a target for good (`confirm` repeats the name) | `target_destroy(name, confirm)` |

Call `target_status(name)` after `target_down` or `ssh_failed` errors, and `target_start(name)` if it is stopped.

## Build, run, debug

| You want to | Use |
|---|---|
| Compile with the SDP (make, cmake or one qcc call); errors come back as `diagnostics[]` with file, line, column, severity, message | `build(path, system, arch, sources, output, cflags, ldflags, make_args)` |
| Run a program once with a deadline; get `outcome`, `exit_code` or `signal_name`, stdout, stderr | `deploy_and_run(target, binary, args, timeout_s, env, cwd)` |
| Turn a crash (SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGABRT) into a backtrace with file, line, locals, source lines and every thread's stack | `get_crash(target, run_id)` |
| Analyse a core that qnx-mcp did not produce | `get_crash(target, core_path, binary)` with the exact unstripped binary that crashed |
| Start a service or any long-running program and return at once | `run_start(target, binary, args, timeout_s)` |
| Read its new output and see whether it is still running | `run_poll(target, run_id, stdout_offset, stderr_offset, wait_s)` |
| Stop it | `run_stop(target, run_id)` |
| Copy a data or config file to the target (below `/tmp` or `/data`) | `file_put(target, local_path, remote_path, mode, overwrite)` |
| Copy a file back from the target (output, a log file, a generated report) | `file_get(target, remote_path, local_path, overwrite)` |
| See what is in a target directory: what a service installed, which cores dumper wrote (`/data/var/dumper`), whether a file exists | `file_list(target, path, max_entries)` |
| Run a program that is already installed on the target, for example to restart a service | `deploy_and_run(target, remote_binary, args, timeout_s)` or `run_start(target, remote_binary, args)` |
| Backtrace of a crash of such a program (qnx-mcp has no copy of it, so pass the host path of its unstripped build) | `get_crash(target, run_id, binary)` |

## Look inside the system

| You want to | Use |
|---|---|
| See why something hangs or what a service is doing: every thread's state and what it is blocked on | `processes(target, name, include_threads=true)` |
| Read the system log (slog2): messages of one process, by time, severity or text | `logs(target, process, since_s, min_severity, contains, limit)` |
| Stop a process that qnx-mcp did not start (a hung program, a service you want to restart); only executables below `/tmp` or `/data` | `process_stop(target, pid, signal, grace_s, kill_after_grace)` |

## Recipes

**Crash.** `build(path)` then `deploy_and_run(target, binary=artifacts[0].path, args=[...])`. If `outcome` is `crashed`, call `get_crash(target, run_id)` with that `run_id`; do not look for the core yourself, dumper told the runner where it is. Read `summary`, `user_frame` (innermost frame in your sources) and its `locals`: a pointer local of `0x0`, or a small `fault_address` such as `0x8`, means a NULL pointer, the small number being the offset of the field that was read. Then look at the caller's frame and locals to see where the bad value came from, and only then change the code. After a fix, rebuild and run again: a `core_mismatch` from `get_crash` means you passed a different build than the one that crashed.

**Hang.** Start the program with `run_start`, give it a moment, then `run_poll` (no new output is a hint, not proof) and `processes(target, name, include_threads=true)`. Thread states on QNX:

* `RECEIVE`: a server thread waiting in `MsgReceive` for a client. Normal for an idle server.
* `SEND`: a client called `MsgSend` and the server has not received the message yet; the server is busy or stuck. `blocked_on` is the server's pid.
* `REPLY`: the server has received the message and has not replied yet. `blocked_on` is the server's pid.
* `MUTEX`: waiting for a mutex; `blocked_on` shows the mutex address and the owner as `pid-tid`.
* `CONDVAR`, `SEM`, `JOIN`, `NANOSLEEP`, `READY`, `RUNNING`: waiting on a condition variable, a semaphore, a thread to exit, a timer; or runnable.

Follow the chain: a REPLY-blocked client points at a server; look at that server's threads; a MUTEX-blocked thread points at the owner thread; a cycle is a deadlock. Stop the run with `run_stop`, fix, rebuild, start again and check that the server threads are back in RECEIVE.

**Service and logs.** `run_start` the service, `run_poll` with `stdout_offset` set to the previous `stdout_next` to read only new output, `logs(target, process=<name>, min_severity="warning")` for what it logged, `run_stop` at the end. Programs log to slog2 buffers in memory, not to files. When a program ends, slogger2 keeps its buffer set on a delete queue of 16 (default) and drops the oldest when more arrive; every SSH login, so every qnx-mcp tool call, adds a few. Read the log while the program runs, or right after it ends.

**No shell on the target.** Everything a task needs goes through these tools. Find things with `processes` (the `path` column is where each executable lives; most paths are shown without the leading `/`, for example `tmp/hub`) and `file_list`; read files with `file_get`; write them with `file_put`. To restart a service: `processes(target, name)` for its pid and path, `process_stop(target, pid)` (if `still_running` is true, call it again with `kill_after_grace=true`), fix and rebuild, then `run_start(target, binary=<new build>)`, or `run_start(target, remote_binary=<its path>)` to start the installed program again, and check `logs(target, process=<name>)`. A crash of a program started with `remote_binary` needs `get_crash(target, run_id, binary)`; the result says so in `next_step`. Its `provenance.verified` is true only when the build ID in the core matches your binary; `verified: false` with `evidence: "pid and path match; build ID not available"` means the backtrace may name wrong lines, so say so in your answer.

## Rules

* `deploy_and_run` and `run_start` copy only the executable. Copy data files with `file_put` first and pass target paths in `args`.
* `args` is an argv array, never a shell command line: no pipes, redirections or globbing.
* Build for the target's architecture (`arch` of `build` must match `target_status(name).arch`).
* `build` runs the project's own make or cmake files on the build host. Only build trees you trust.
* There is no shell tool on purpose. If a task needs something the tools cannot do, say so and ask the user, instead of working around the server with your own ssh commands.
* `target_not_allowed` means the target is outside the server's allowlist (`QNX_MCP_ALLOW_TARGETS`). Do not try other names; ask the user.
* `target_busy` means another call holds the target's lock (for example a long `target_create`); wait and retry.
* `process_not_allowed` means `process_stop` will not touch that process (pid 1, procnto, a critical process, qnx-mcp's runner, or an executable in `/proc/boot`, `/bin`, `/sbin`, `/usr`, `/system` or outside `/tmp` and `/data`). Do not look for another way to stop it; report it. Use `run_stop` for runs that qnx-mcp started.
* `process_stop` identifies the process by pid and start time and checks it again before each signal. `process_not_found` saying it changed means the pid now belongs to another process or the program called exec: call `processes` again. An error whose `details.signal_sent` is true means a signal did go out (for example SIGKILL was withheld because the program changed, or its state could not be read): check with `processes` before you act again.
* `remote_binary` cannot point into `/tmp/qnx-mcp` (qnx-mcp's own files), also not through a symlink.
* Programs run as root on the target. Use disposable development targets.
