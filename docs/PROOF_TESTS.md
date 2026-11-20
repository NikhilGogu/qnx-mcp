# Proof tests: baseline against qnx-mcp on a real target

Two live tests put a plain way of doing the job next to the qnx-mcp tool and count what happens on a QNX 8.0 target. They skip when no target is set, like the other end-to-end tests. They are not part of `scripts/live-tests.sh`: the first one runs about 300 timed-out programs at the default size.

Both need the QNX SDP on the host and a running x86_64 QEMU target made by `target_create` (login `root`, dumper running as `target_create` starts it).

```sh
. <env that sets QNX_MCP_TARGETS_DIR, QNX_MCP_STATE_DIR, QNX_MCP_SDP_ENV>
export QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*' QNX_MCP_E2E_TARGET=tb QNX_MCP_PROOF_OUT=/some/dir
cd tests
python -m pytest -q -s -p no:asyncio test_proof_orphan_count_e2e.py
QNX_MCP_PROOF_ROUNDS=5 python -m pytest -q -s -p no:asyncio test_proof_stale_core_e2e.py
```

`QNX_MCP_PROOF_OUT` is optional. When set, each test writes its numbers there as JSON. Run the two tests one after the other on one target, or on two targets, never at the same time on the same target: the stale core test clears dumper's core directory.

## Results

One run of each test on a QNX 8.0.0 x86_64 QEMU target made by `target_create` (login `root`), with the default of 50 runs for `orphan_count` and `QNX_MCP_PROOF_ROUNDS=5` for `stale_core_backtrace`. Counts only.

| Test | Plain way | qnx-mcp |
|---|---|---|
| `orphan_count`, 50 timed-out runs of `hang` | plain `ssh` left 50 copies | `deploy_and_run` left 0 |
| `orphan_count`, 50 runs of the `setsid` program | plain `ssh` left 100 processes | `deploy_and_run` left 50 (one escaped child per run) |
| `stale_core_backtrace`, 40 cases | gdb warned in all 40 and gave the correct stack in 0: 30 with no function named, 10 with a named but wrong stack | `get_crash` refused all 40 with `core_mismatch`; it printed no wrong or unverified stack |

With `ssh -tt`, the 50 `hang` runs left 24 copies and the 50 `setsid` runs left 74 processes. The raw output of the run is not stored in this repository.

## orphan_count (`tests/test_proof_orphan_count_e2e.py`)

Question: after N runs of a program that hangs and is stopped by a timeout, how many copies are still running on the target?

* Baseline: `timeout 10 ssh root@target ./hang`, and the same with `ssh -tt`. It uses the key, pinned host key and options that qnx-mcp itself passes to ssh, so only the way of running differs.
* qnx-mcp: `deploy_and_run` with `timeout_s=10`.
* Count: `pidin -p <name>` on the target, after all runs and after a short wait. The test also counts after the first plain run.
* Second program: it forks a child that calls `setsid()` and then hangs. README says a child that leaves the process group survives the timeout. The test counts that case for all three ways.

The test settles first whether QNX sshd already ends the remote program when ssh goes away. If a plain run leaves nothing behind, the test skips, because then there is nothing to show.

`QNX_MCP_PROOF_RUNS` (default 50) is N. The baseline ssh runs go five at a time to save wall time; `QNX_MCP_PROOF_PARALLEL=1` runs them one after the other. `deploy_and_run` always runs one at a time. The assertions are: the plain ssh baseline leaves N copies of `hang`, `deploy_and_run` leaves 0, and for the `setsid` program `deploy_and_run` leaves N (one escaped child per run). The `ssh -tt` numbers are reported and not asserted. With a pty, closing the connection can send SIGHUP to the program, and on the test image it did so only for some of the runs, so that variant leaves a part of the programs. That is sshd's behaviour and the test records it as it is.

## stale_core_backtrace (`tests/test_proof_stale_core_e2e.py`)

Question: when a core and a binary do not belong together, what does the tool print?

* Baseline: `ntox86_64-gdb -batch -ex bt <binary on the host now> <newest core>`.
* qnx-mcp: `get_crash(core_path=<newest core>, binary=<same binary>)`.
* Truth: gdb with the binary that really crashed.

Two crash programs (a NULL table read, a division by zero) times four situations:

| Situation | What happened | Core | Binary given |
|---|---|---|---|
| `rebuilt_after_crash` | crash, then an edit and a rebuild | the crash | the new build |
| `two_crashes` | crash, then another program crashes | the other program's | the first program's build |
| `leftover_old_core` | crash, then a clean `-O2` rebuild runs | the old crash | the `-O2` build |
| `no_build_id` | like the first, both linked with `--build-id=none` | the crash | the new build |

A gdb result is `correct` (same stack as the truth), `wrong_named` (at least one named function, different stack) or `unreadable` (no function named at all). gdb's own "core file may not match specified executable file" warning is recorded for every case. A `get_crash` result is `refused` when it returns `core_mismatch`. Two controls must pass: `get_crash` with the right binary and the same core prints the true stack, and a run made by `deploy_and_run` followed by `get_crash(run_id)` prints it too.

`QNX_MCP_PROOF_ROUNDS` (default 1) repeats the eight cases. gdb's output depends on where the loader put the programs, so one round is a sample.

What this does not show:

* QNX x86_64 programs are position independent. With a wrong binary gdb usually cannot place the code, prints `?? ()` frames and says the core may not match. That is a loud failure, not a plausible wrong stack. A named but wrong stack showed up only when the other program's addresses landed in the binary's range. A binary linked at a fixed address was not tried.
* The test does not pick the gdb options a careful developer would add (for example matching build IDs by hand). Plain gdb is the baseline.
