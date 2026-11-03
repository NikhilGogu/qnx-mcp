# Dry runs of the codelab

Date: 3 November 2026. Each pass follows `qnx-mcp-agent-target.md` from the top in a new workspace on the build box (Ubuntu 22.04.5, QNX SDP 8.0 in `~/qnx800`, QEMU 6.2.0 with KVM, Python 3.10.12): a new `CODELAB` directory, a new venv with qnx-mcp installed from a clone, a new `QNX_MCP_TARGETS_DIR` and `QNX_MCP_STATE_DIR`, a new Claude Code config directory, and the target `cl1` allowed by its own allowlist. Paths in home directories are shortened to `~`.

Where the codelab says to ask the agent, the prompt was given, as written, to a headless Claude Code 2.1.312 session on Amazon Bedrock (model `global.anthropic.claude-sonnet-5-5`, default effort), one `claude -p` turn per prompt, each turn continuing the previous conversation (`--continue`), like one interactive session. Permissions: `--permission-mode acceptEdits --allowedTools mcp__qnx`, standing in for a reader who answers "Yes, and don't ask again" for the qnx tools. The driver scripts and full transcripts stayed on the box (`~/cl-dryrun/pass<N>/logs/`).

Deviations from the text, the same in every pass:

* `CODELAB` was `~/cl-dryrun/pass<N>/qnx-codelab` instead of `~/qnx-codelab`, so passes do not share state.
* `git clone https://github.com/NikhilGogu/qnx-mcp.git` was redirected (`url.<bundle>.insteadOf` in a private `GIT_CONFIG_GLOBAL`) to a git bundle of the local repository, because the codelab files are not pushed yet. The public repository answered (`git ls-remote` showed `main` at 06e4aef) but lacks `codelab/` and `skills/`.
* `sudo apt install ...` ran as `apt-get -s install ...` (simulated: the box is shared and nothing may be upgraded); all packages were already installed. `sudo adduser` ran with `sudo -n` (the user was already in both groups). Logging out and in was not needed.
* The interactive `claude` start was replaced by the headless turns. The Codex section was not run in a session; its configuration block was checked with `codex mcp list` (Codex CLI 0.160.0, temporary `CODEX_HOME`), which listed `qnx` as `enabled` with `tool_timeout_sec: 600`.
* `claude` was a wrapper that sets the Bedrock environment and the pass's `CLAUDE_CONFIG_DIR`; `MCP_TOOL_TIMEOUT` was not set (Claude Code's default).

Costs are the `total_cost_usd` of the last turn of each pass (cumulative over the session).

## Pass 0: 17:36 to 17:39 UTC, stopped after step 6

| Step | Result |
|---|---|
| 3 Install | The first clone failed: `warning: remote HEAD refers to nonexistent ref`. Harness error (the bundle had no `HEAD`), not the text; bundle recreated, step repeated: 9 s. |
| 4 to 6 | As written. |
| 6 Fix the compile error | While fixing the typo the agent read `sensor.c` and said that `find_calibration()` returns NULL for an unknown channel and nothing checks it. The crash in that version came from a channel name with a trailing space in `samples.csv`, so the source gave the bug away. |

Fix: the example now crashes on valid input. `find_calibration()` uses `bsearch()` over a table that is not sorted (`gyro_z` before `baro`), so the lookup of `gyro_z` fails although the channel is in the table and the data is clean. Steps 7 and 8 rewritten for it. Bedrock cost of pass 0: $0.13.

## Pass 1: 17:40 to 17:46 UTC

| Step | Time | Result |
|---|---|---|
| 2 Before you begin | 0 s | `check-net` exit 0, `virsh` shows `default` active |
| 3 Install | 10 s | `--check`: every line `ok`, `environment_check: ok` |
| 4 Connect | 1 s + 9.5 s | `claude mcp list`: `qnx: ... - ✔ Connected`; agent listed 17 tools, `environment_check` passed |
| 5 Create a target | 25.3 s | `target_create` ok, `host_key_policy: image`, dumper with `-n` |
| 6 Build | 13.4 s + 13.5 s | 2 errors and 1 warning as objects; agent fixed `gain` to `scale`; build ok with build ID |
| 7 Crash | 26.4 s | `outcome: crashed`, SIGSEGV, `core_ready_ms: 0`; `get_crash`: `calibrate` at `sensor.c:71`, fault address `0x8`, `line_no` 7 `"gyro_z,31"`, provenance verified; agent named the unsorted table and `bsearch()` |
| 8 Fix | 19.6 s | Agent sorted the table and added a NULL check with a slog2 warning; `exit_code` 0, `20 readings from 23 lines` |
| 9 Service | 15.8 s | `run_start`, two `run_poll` calls, the second from `stdout_offset` 312 |
| 10 Logs | 10.9 s | 4 warnings `line 12: gyro_z reading 10.71 rad/s is outside +/-8.70`; `run_stop` `outcome: stopped` |
| 11 Hang | 29.1 s + 19.8 s | JOIN, MUTEX (`2347031-03`), SEND, REPLY; agent explained the cycle; after its fix: RECEIVE, NANOSLEEP, NANOSLEEP and `table` lines |
| 12 Safety | 9.9 s + 8.1 s | `target_not_allowed` for `lab2`; `"/tmp/sensor/samples.csv; reboot"` arrived as one argument, `exit_code` 1, target still running |
| 13 Clean up | 11.5 s | `target_destroy` ok; `claude mcp remove qnx`; directory removed |

The agent made 30 qnx tool calls and 5 read-only Bash calls on host files (`ls`, `cat`, `grep`, `sed -n`); none used ssh. Bedrock cost of pass 1: $0.47.

Text fixed after pass 1:

1. Step 9: the agent noticed that `run_poll` with `wait_s=5` returned after about 2 s and did not know why. Added that `wait_s` returns as soon as new output has arrived.
2. Step 13: `pgrep -f qemu-system` lists every QEMU process on the machine. The check is now `pgrep -af qemu-system | grep qnx-codelab`, which prints nothing.
3. Step 5: the time is now the measured 25 s for the whole step (the draft had 21 s from a development run).
4. Step 10 and the skill: the draft guessed why the log of an exited program disappears. Replaced with slogger2's documented delete queue (`-Q`, 16 orphaned buffer sets by default) and what we observed (gone after a few tool calls, still there after 150 s on an idle target).
5. Rendering (claat export checked in headless Chromium): long prompts ran past the code block, so they are wrapped at 88 columns; JSON outputs are formatted to fit, and locals keep their real `{name, value}` shape; a numbered list in step 7 that code blocks interrupted became paragraphs.
6. Codex: `codex mcp add` followed by appending `tool_timeout_sec` with `echo` depended on the table being last in the file. Replaced by one heredoc TOML block, checked with `codex mcp list`.

## Pass 2: 17:54:43 to 17:58:16 UTC (3 min 33 s)

The text after the six fixes above (commit bc2dac8), run by `pass2.sh`, which `gen_pass.py` generates from the codelab: every `bash` block runs as written apart from the substitutions listed at the top, and every prompt goes to the agent in order.

| Step | Time | Result |
|---|---|---|
| 2 Before you begin | 0.5 s | all packages already the newest version (simulated), user already in `kvm` and `libvirt`; `virsh` lists `default` as `active`; `check-net` printed nothing |
| 3 Install | 9.2 s | `ls` shows `hub` and `sensor`; `--check`: 15 lines `ok`, `environment_check: ok` |
| 4 Connect | 1.4 s + 9.2 s | `claude mcp list`: `qnx: ... - ✔ Connected`; the session listed the `qnx-mcp` skill and 17 qnx tools; the agent listed the 17 tools by name (it did not count them) and called `environment_check`: "All 15 checks passed" |
| 5 Create a target | 27.4 s | `target_create`, then `target_status`: `running`, `host_key_policy: image`, dumper with `-n` |
| 6 Build | 9.7 s + 11.6 s | 2 errors and 1 warning as objects; the agent changed `gain` to `scale`; build ok, `debug_info: true`, build ID. Unasked, it added that `bsearch()` runs over a table that is not sorted and that nothing checks its result for NULL, and guessed that a `baro` line would crash; it changed nothing else |
| 7 Crash | 19.7 s | `file_put` 376 bytes; `outcome: crashed`, SIGSEGV, `core_ready_ms: 0`; `get_crash`: `calibrate` at `sensor.c:71`, fault address `0x8`, `line_no` 7 `"gyro_z,31"`, `verified: true`; the agent named the unsorted table, `bsearch()` and the missing NULL check, and traced the search for `gyro_z` element by element. It called the field at offset 8 `offset`; it is `scale` |
| 8 Fix | 19.6 s | the agent sorted the table (with a comment that it must stay sorted) and added a NULL check with a slog2 warning; `exit_code` 0, `20 readings from 23 lines`; `diff -u` shows those changes |
| 9 Service | 15.7 s | `run_start`; `run_poll` (`wait_s` 2) returned bytes 0 to 240, the second poll from `stdout_offset` 240 returned bytes 240 to 457; `state: running` |
| 10 Logs | 11.5 s | 3 warnings `line 12: gyro_z reading 10.71 rad/s is outside +/-8.70`, one per pass through the file; `run_stop` `outcome: stopped` |
| 11 Hang | 25.8 s + 19.9 s | only the first line, then nothing; JOIN, MUTEX (`2404375-03`), SEND, REPLY; the agent explained the cycle and stopped the run; after its fix (sample into a local table, lock only for the copy, `MsgSend()` after unlocking): RECEIVE, NANOSLEEP, NANOSLEEP and `table` lines |
| 12 Safety | 9.1 s + 12.2 s | `target_not_allowed` for `lab2`; `"/tmp/sensor/samples.csv; reboot"` arrived as one argument, `exit_code` 1, `No such file or directory`; the target still running with the same QEMU pid |
| 13 Clean up | 9.2 s + 0.2 s | `target_destroy` ok; `claude mcp remove qnx`; `ls` reports that the directory does not exist; `pgrep -af qemu-system \| grep qnx-codelab` printed nothing |

The agent made 30 qnx tool calls, 2 Read and 5 Edit calls on the work files, and 5 read-only Bash calls on host files (`ls`, `grep`, `cat`, `sed -n`, and `cat hub/Makefile`, which failed because `hub` has no Makefile); none used ssh. Bedrock cost of pass 2: $0.47.

Verdict: every command and prompt ran as written and every Check in the text held. One statement in the text was wrong. Step 7 said that reading the source does not tell you that the run will crash. In step 6 the agent, while fixing the typo, read the lines around `struct calibration`, saw the table and `bsearch()`, and suspected a crash (on the wrong channel, `baro`). Reading pass 1's transcript again showed the same remark in pass 1's step 6, which the pass 1 notes above missed; there the agent also asked whether to fix both problems before running, and a reader who said yes would have had no crash in step 7.

Text fixed after pass 2 (commit 26f5982):

1. Step 6: the prompt is now "Fix only the compile error and build again.", followed by a sentence that the agent may mention other problems in `sensor.c` and offer to fix them, and that the reader should leave them for now.
2. Step 7: the paragraph after the backtrace says that the source can make you suspicious (in our runs the agent noticed the table and guessed `baro`), and that the core shows what did happen: the crash on line 7 of the input, the first `gyro_z` reading.
3. Step 7: the field at offset 8 is `scale` "(the pointer `channel` takes the first 8 bytes)", so a reader can check an agent that says otherwise, as the pass 2 agent did.

The prompt changed, so pass 3 runs the whole codelab again.

## Pass 3: 19:26:41 to 19:31:05 UTC (4 min 24 s)

The text at commit 26f5982, run by `pass3.sh` (generated the same way; it differs from `pass2.sh` only in the step 6 prompt).

| Step | Time | Result |
|---|---|---|
| 2 Before you begin | 0.5 s | as in pass 2 |
| 3 Install | 9.2 s | `ls` shows `hub` and `sensor`; `--check`: 15 lines `ok`, `environment_check: ok` |
| 4 Connect | 1.4 s + 12.5 s | `qnx: ... - ✔ Connected`; the agent listed "17" tools by name and called `environment_check`: "All 15 checks passed" |
| 5 Create a target | 33.7 s | `target_list` (no targets), `target_create`, `target_status`: `running`, `host_key_policy: image`, dumper with `-n` |
| 6 Build | 9.8 s + 32.4 s | 2 errors and 1 warning as objects. With "Fix only the compile error" the agent changed only `gain` to `scale` (a second edit and build put back a space its first edit had dropped); build ok, `debug_info: true`. It had read the whole file, named the unsorted table and the missing NULL check without naming a channel, and "left both alone, as you asked" |
| 7 Crash | 24.0 s | `outcome: crashed`, SIGSEGV, `core_ready_ms: 0`; `get_crash`: `calibrate` at `sensor.c:71`, fault address `0x8` read as `scale`, `line_no` 7 `"gyro_z,31"`, `verified: true`; the agent named the unsorted table, `bsearch()` and the missing NULL check. Its account of the order was wrong ("`baro` sorts first"), and it said it had not traced the comparisons; it corrected the order itself in step 8 |
| 8 Fix | 25.5 s | sorted table with a comment, NULL check with a slog2 warning; `exit_code` 0, `20 readings from 23 lines`; unasked, the agent also read the program's slog2 buffer |
| 9 Service | 16.0 s | `run_start`; `run_poll` (`wait_s` 2) returned bytes 0 to 217, the second poll from `stdout_offset` 217 returned bytes 217 to 504; `state: running` |
| 10 Logs | 12.9 s | 4 warnings `line 12: gyro_z reading 10.71 rad/s is outside +/-8.70`, about 4 s apart; `run_stop` acknowledged, `outcome: stopped` |
| 11 Hang | 28.9 s + 21.3 s | only the first line, then nothing; JOIN, MUTEX (`2564119-03`), SEND, REPLY; the agent explained the cycle and stopped the run; after its fix (the same shape as in pass 2): RECEIVE, NANOSLEEP, NANOSLEEP and `table` lines |
| 12 Safety | 9.1 s + 12.4 s | `target_not_allowed` for `lab2`; the agent said it had not tried to get around the allowlist; `"/tmp/sensor/samples.csv; reboot"` arrived as one argument, `exit_code` 1, the target still running with the same QEMU pid |
| 13 Clean up | 14.1 s + 0.2 s | `target_destroy` ok; `claude mcp remove qnx`; `ls` reports that the directory does not exist; `pgrep -af qemu-system \| grep qnx-codelab` printed nothing |

The agent made 33 qnx tool calls, 2 Read and 6 Edit calls on the work files, and 3 read-only Bash calls (`ls` twice, `cat samples.csv`); none used ssh. Bedrock cost of pass 3: $0.52.

Over passes 1 to 3 no session called the Skill tool: Claude Code listed `qnx-mcp` among the skills of every session, and the agent never loaded it.

Verdict: every command and prompt ran as written and every Check held, including the new step 6 prompt. Pass 3 needed no change to a command, a prompt or a Check. It led to four changes of prose, made after the pass (in the commit that adds this file):

1. Step 4: a skill is loaded when the agent decides a task needs it; in our runs the agent never loaded it, because the tool descriptions were enough.
2. Step 6: the agent mentioned other problems in every run (the earlier "ours did" was less exact).
3. Step 7: the agent noticed the table in each run but could not say which lookups would fail (twice it guessed `baro`; in pass 3 it named no channel).
4. Step 7, Check: compare the agent's details with the text; in two runs it got one wrong (the field at offset 8 in pass 2, the sort order in pass 3).

These changes touch no `bash` block and no prompt: `gen_pass.py` run on that text produces a driver byte for byte identical to `pass3.sh` (`cmp` silent). The outputs shown in the codelab come from pass 1; pass 3 reproduced each of them with its own ids, addresses and times.

One Check was wrong in every pass so far. Step 13 said that `pgrep -af qemu-system | grep qnx-codelab` prints nothing when no QEMU from the codelab is left. qnx-mcp starts QEMU through `mkqnximage --run` in the target's directory, and QEMU's command line holds only relative paths (`-drive file=output/disk-qemu.vmdk ...`), so that check prints nothing even while the target runs. Tested on a temporary development target `cl0` (`~/cl-dev/checktest.sh`): while it ran, the old check printed nothing (`grep` exit 1) and a check by working directory printed `~/cl-dev/targets/cl0`; after `target_destroy` the new check printed nothing. That passes 1 to 3 left no QEMU behind rests on `target_destroy` ("stopped QEMU", checked by qnx-mcp through the pidfd) and on the same check by working directory after pass 3, which found no QEMU under any dry-run directory.

Text fixed after pass 3 (commit 81a83cf): step 13's Check is now `for p in $(pgrep qemu-system); do echo "$p $(readlink /proc/$p/cwd)"; done | grep qnx-codelab`, with a sentence on why the working directory and not the command line identifies the codelab's QEMU, and what to do with a line it prints. It is a `bash` block, so the driver runs it.

## Pass 4: 19:35:57 to 19:39:54 UTC (3 min 57 s)

The final text (commit 81a83cf), run by `pass4.sh`, which differs from `pass3.sh` only in step 13: the new check replaces the old one.

| Step | Time | Result |
|---|---|---|
| 2 Before you begin | 0.5 s | as in pass 2 |
| 3 Install | 9.2 s | `ls` shows `hub` and `sensor`; `--check`: 15 lines `ok`, `environment_check: ok` |
| 4 Connect | 1.4 s + 10.5 s | `qnx: ... - ✔ Connected`; the agent listed the tools by name and called `environment_check`: "passed on all 15 checks" |
| 5 Create a target | 25.9 s | `target_create`, `target_status`: `running` |
| 6 Build | 12.3 s + 10.1 s | 2 errors and 1 warning as objects; the agent changed `gain` to `scale`, "I changed nothing else"; build ok, `debug_info: true`; it named the unsorted table and the missing NULL check without naming a channel and "left them alone, as you asked" |
| 7 Crash | 27.1 s | `outcome: crashed`, SIGSEGV, `core_ready_ms: 0`; `get_crash`: `calibrate` at `sensor.c:71`, fault address `0x8` read as `offsetof(struct calibration, scale)`, `line_no` 7 `"gyro_z,31"`, `verified: true`; the agent named the unsorted table, `bsearch()` and the missing NULL check |
| 8 Fix | 21.6 s | sorted table with a comment, NULL check with a slog2 warning; `exit_code` 0, `20 readings from 23 lines` |
| 9 Service | 24.3 s | `run_start`; `run_poll` (`wait_s` 2) returned bytes 0 to 937, the second poll from `stdout_offset` 937 returned bytes 937 to 1320; `state: running` |
| 10 Logs | 13.5 s | 6 warnings `line 12: gyro_z reading 10.71 rad/s is outside +/-8.70`; `run_stop` `outcome: stopped` |
| 11 Hang | 25.5 s + 26.1 s | only the first line, then nothing; JOIN, MUTEX (`2347031-03`), SEND, REPLY; after the agent's fix: RECEIVE, NANOSLEEP, NANOSLEEP and `table` lines |
| 12 Safety | 9.5 s + 9.6 s | `target_not_allowed` for `lab2`; `"/tmp/sensor/samples.csv; reboot"` arrived as one argument, `No such file or directory`, the target still running |
| 13 Clean up | 9.4 s + 0.2 s | `target_destroy` ok; `claude mcp remove qnx`; `ls` reports that the directory does not exist; the new check printed nothing |

All Checks held, and no fix was needed: pass 4 is a full run of the final text. Bedrock cost of pass 4: $0.50.

## Summary

| Pass | Text | Wall time | Bedrock | Fixes after it |
|---|---|---|---|---|
| 0 | draft | stopped after step 6 | $0.13 | example rewritten (the source gave the crash away) |
| 1 | 137c35c examples, draft text | 17:40 to 17:46 | $0.47 | six text fixes (bc2dac8) |
| 2 | bc2dac8 | 3 min 33 s | $0.47 | step 6 prompt and two sentences in step 7 (26f5982) |
| 3 | 26f5982 | 4 min 24 s | $0.52 | four changes of prose (611e5ce), step 13 Check (81a83cf) |
| 4 | 81a83cf | 3 min 57 s | $0.50 | none |

Wall time counts the commands and agent turns from the first `bash` block to the end, against the 45 minutes the codelab gives a reader, who also reads and types. Bedrock cost over all passes: $2.09. The dry-run targets (`cl1` in each pass) were destroyed in step 13, and the development target `cl0` (its own allowlist and directories) with `target_destroy` after the check test.
