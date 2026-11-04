---
name: qnx-mcp
description: Which qnx MCP tool (mcp__qnx__*) to use to build, run, debug crashes and hangs, and read logs on the QNX target. Read this before building or running anything when the qnx MCP tools are available.
---

# qnx-mcp tools: when to use which

The `qnx` MCP server drives the target named in TARGET.md with typed, bounded tools. They are deferred: load the ones you need first, for example ToolSearch with `select:mcp__qnx__build,mcp__qnx__deploy_and_run,mcp__qnx__get_crash,mcp__qnx__processes,mcp__qnx__logs,mcp__qnx__target_status`.

| You want to | Use |
|---|---|
| Check the target is up (state, IP, dumper running) | `target_status(name)` |
| Build with the QNX SDP (make, cmake or one qcc call) and get errors as file/line objects | `build(path)` |
| Run a program on the target with a time limit; get exit code or signal, stdout, stderr | `deploy_and_run(target, binary, args, timeout_s, cwd)` |
| Turn a crash (SIGSEGV, SIGABRT, SIGFPE, SIGBUS) into a backtrace with file, line, locals and the crashing thread | `get_crash(target, run_id, binary)` with the run_id of the crashed run |
| See why something hangs: every thread's state (MUTEX, CONDVAR, SEND, REPLY, RECEIVE, NANOSLEEP) and what it is blocked on | `processes(target, name, include_threads=true)` |
| Read the system log (slog2) of a process, by time, severity or text | `logs(target, process, since_s, min_severity, contains)` |

Notes:

- `deploy_and_run` copies only the binary. Data files must already be on the target (copy them with scp, see TARGET.md); arguments are paths on the target.
- A run that hangs is killed at `timeout_s`. To inspect a hang, start the program in the background over SSH (`nohup ./prog > /tmp/prog.log 2>&1 &`), call `processes`, then stop it. Services already running on the target can be inspected the same way.
- Pass the run_id of the crashed `deploy_and_run` to `get_crash`; the core comes from dumper, so there is no need to look for it.
- Use SSH for everything else: reading files and logs on the target, copying data, starting services.
