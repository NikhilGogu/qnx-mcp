# Least-privilege study (phase 3), 3 and 4 November 2026

Question: can an agent with qnx-mcp and no shell on the target fix the same QNX bugs as an agent with a root shell over SSH?

Design (preregistered in the phase 3 amendment of [PROTOCOL.md](../../PROTOCOL.md), committed before any trial): arm A has the QNX agent skills, Bash on the build box and a root shell on its own QNX 8.0 QEMU target over SSH. Arm C has the same skills plus qnx-mcp v0.2.1 (commit `249d08a`, cloned from GitHub into a frozen copy on the box, [server-snapshot.txt](server-snapshot.txt)) and its v0.2.1 skill file; Bash on the build box stays, but the agent's ssh, scp and sftp refuse every host and its `TARGET.md` says there is no shell access. Same model, prompt, caps (20 min, 80 turns, 1.50 USD) and the eight v2 tasks. Claude Sonnet 5.5 and Claude Haiku 5.5 on Amazon Bedrock, 5 trials per task, arm and model, 160 trials in one randomized paired order (seed 20261107), at most 4 at once.

## What ran

* Smoke check of arm C ([smoke/](smoke)): qnx-mcp connected with 19 tools, the agent's `ssh <target> uname -a` was refused by the guard, and qnx-mcp's own ssh calls reached only the trial target.
* Pilot, arm C only, Sonnet 5.5, one trial per task ([pilot/](pilot), [pilot-gaps.md](pilot-gaps.md)): 8/8 oracle, 8/8 root cause. No task was impossible without a shell, so all eight stayed in the study; the limitation found is listed below.
* Full study ([study/](study), `study/schedule.json`, `study/summary.md`): `eval.py pilot --name least-privilege-2026-11-03/study --tasks v2-01 ... v2-08 --reps 5 --arms AC --models sonnet haiku --seed 20261107 --max-usd 36`. All 160 scheduled trials ran and were scored: no harness error, no trial skipped by the budget guard, no cap hit, every transcript reports the scheduled Bedrock model, every arm C trial used qnx-mcp `249d08a`.
* Interruptions: none in the study. The controller ran from 22:17:56 UTC on 3 November (schedule created) to 00:04:51 UTC on 4 November (last trial scored); the 160 trial start times have no gap longer than 5 minutes. The agent supervising the run was paused by a usage limit while it ran; that did not stop the controller, so no trial was missing, rerun or discarded and no restart was needed.
* Cost: 17.98 USD for phase 3 (Claude Code list-price estimates: smoke 0.08, pilot 1.63, study 15.31, blind judge 0.95), under the 40 USD cap.

## Headline

| model | arm | oracle pass | exact 95% interval | root cause (blind judge) | median wall s [range] | median turns [range] | median cost USD [range] |
|---|---|---|---|---|---|---|---|
| Sonnet 5.5 | A (root shell) | 40/40 | 0.91-1.00 | 39/40 | 42 [26-746] | 10 [2-16] | 0.123 [0.087-0.194] |
| Sonnet 5.5 | C (qnx-mcp, no shell) | 40/40 | 0.91-1.00 | 40/40 | 51 [32-144] | 20 [13-43] | 0.198 [0.128-0.354] |
| Haiku 5.5 | A (root shell) | 40/40 | 0.91-1.00 | 40/40 | 103 [60-332] | 16 [10-44] | 0.019 [0.013-0.038] |
| Haiku 5.5 | C (qnx-mcp, no shell) | 40/40 | 0.91-1.00 | 39/40 | 115 [66-219] | 37 [20-64] | 0.028 [0.021-0.051] |

Paired (same task and repetition, C minus A): Sonnet 40 pairs, all both pass; median differences +8 s, +10 turns, +0.067 USD. Haiku 40 pairs, all both pass; +4 s, +18 turns, +0.008 USD. Sensitivity check fixed after the pilot (without v2-02, v2-03 and v2-08, where `process_stop` refused services): 25/25 in every arm and model.

## Tables (`python3 harness/lp_report.py results/least-privilege-2026-11-03/study --exclude v2-02-mp-deadlock v2-03-resmgr-slog2 v2-08-fd-leak`)


### sonnet: per task and arm

Medians with [minimum-maximum]. Tokens are input (uncached, cache write, cache read) plus output, in thousands.

| task | arm | n | oracle pass | root cause | wall s | turns | tokens (k) | cost USD |
|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 5 | 5/5 | 5/5 | 35 [29-42] | 11 [9-13] | 265 [254-315] | 0.127 [0.116-0.136] |
| v2-01-config-overflow | C | 5 | 5/5 | 5/5 | 44 [38-48] | 18 [17-20] | 475 [442-518] | 0.189 [0.178-0.198] |
| v2-02-mp-deadlock | A | 5 | 5/5 | 5/5 | 38 [29-42] | 11 [10-13] | 233 [197-292] | 0.114 [0.101-0.122] |
| v2-02-mp-deadlock | C | 5 | 5/5 | 5/5 | 132 [127-144] | 33 [29-43] | 840 [732-1065] | 0.266 [0.251-0.354] |
| v2-03-resmgr-slog2 | A | 5 | 5/5 | 5/5 | 172 [34-746] | 10 [2-11] | 227 [75-290] | 0.146 [0.118-0.194] |
| v2-03-resmgr-slog2 | C | 5 | 5/5 | 5/5 | 47 [43-58] | 24 [19-29] | 569 [315-827] | 0.202 [0.160-0.255] |
| v2-04-deep-expr | A | 5 | 5/5 | 4/5 | 32 [26-65] | 10 [8-11] | 248 [178-298] | 0.123 [0.105-0.139] |
| v2-04-deep-expr | C | 5 | 5/5 | 5/5 | 34 [33-40] | 14 [13-16] | 394 [351-445] | 0.162 [0.155-0.180] |
| v2-05-hang-states | A | 5 | 5/5 | 5/5 | 44 [40-60] | 8 [7-10] | 162 [131-225] | 0.093 [0.087-0.113] |
| v2-05-hang-states | C | 5 | 5/5 | 5/5 | 50 [32-62] | 18 [13-23] | 398 [242-467] | 0.161 [0.128-0.202] |
| v2-06-worker-null | A | 5 | 5/5 | 5/5 | 33 [30-43] | 10 [9-15] | 279 [218-421] | 0.131 [0.123-0.151] |
| v2-06-worker-null | C | 5 | 5/5 | 5/5 | 39 [35-53] | 18 [16-21] | 422 [308-600] | 0.177 [0.150-0.199] |
| v2-07-cmake-cross | A | 5 | 5/5 | 5/5 | 48 [44-56] | 16 [15-16] | 423 [416-486] | 0.161 [0.150-0.169] |
| v2-07-cmake-cross | C | 5 | 5/5 | 5/5 | 66 [52-73] | 23 [20-27] | 730 [607-843] | 0.234 [0.188-0.249] |
| v2-08-fd-leak | A | 5 | 5/5 | 5/5 | 42 [33-144] | 9 [8-11] | 207 [161-270] | 0.105 [0.094-0.126] |
| v2-08-fd-leak | C | 5 | 5/5 | 5/5 | 66 [60-72] | 28 [26-30] | 700 [606-721] | 0.227 [0.218-0.250] |
| **all tasks** | A | 40 | 40/40 | 39/40 | 42 [26-746] | 10 [2-16] | 238 [75-486] | 0.123 [0.087-0.194] |
| **all tasks** | C | 40 | 40/40 | 40/40 | 51 [32-144] | 20 [13-43] | 503 [242-1065] | 0.198 [0.128-0.354] |
| **without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak** | A | 25 | 25/25 | 24/25 | 42 [26-65] | 10 [7-16] | 265 [131-486] | 0.129 [0.087-0.169] |
| **without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak** | C | 25 | 25/25 | 25/25 | 43 [32-73] | 18 [13-27] | 447 [242-843] | 0.187 [0.128-0.249] |

### sonnet: totals with exact 95% intervals

| tasks | arm | oracle pass | 95% interval | root cause | total cost USD | wall cap hits |
|---|---|---|---|---|---|---|
| all | A | 40/40 | 0.91-1.00 | 39/40 | 5.06 | 0 |
| all | C | 40/40 | 0.91-1.00 | 40/40 | 8.20 | 0 |
| without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak | A | 25/25 | 0.86-1.00 | 24/25 | 3.19 | 0 |
| without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak | C | 25/25 | 0.86-1.00 | 25/25 | 4.62 | 0 |
| arm C without trials that ran a shell through remote_binary | C | 40/40 | 0.91-1.00 | 40/40 | 8.20 | 0 |

### sonnet: paired (C minus A)

40 complete pairs: both pass 40, only A passes 0, only C passes 0, neither 0.
Median difference C minus A [range]: wall 8 [-700-105] s, turns 10 [1-32], cost +0.067 [-0.002-+0.240] USD.

### sonnet: how arm C worked

* MCP calls per trial: 10 [4-31]; trials with no MCP call: 0; ToolSearch calls per trial: 1 [1-2].
* MCP calls by tool (all 40 trials): deploy_and_run 97, build 63, file_get 51, run_start 50, file_list 39, run_stop 35, processes 30, process_stop 24, target_status 21, file_put 20, get_crash 15, run_poll 12, logs 11.
* Agent ssh/scp/sftp calls refused by the guard: 0 in 0 trials; qnx-mcp ssh/scp calls 1278, hosts other than the trial target: 0.
* qnx-mcp errors returned to the agent: process_not_allowed 24, path_not_allowed 18, timeout 5.
* Programs run with remote_binary: /data/opt/status/statusctl (6), /data/opt/acq/acqctl (3); trials that ran a shell this way: 0, a system utility: 0.

### sonnet: transcript and guard flags (all arms, for review)

* arm A: host-destructive (1), review: disassembly (check whose binary) (1)
* arm C: none

### haiku: per task and arm

Medians with [minimum-maximum]. Tokens are input (uncached, cache write, cache read) plus output, in thousands.

| task | arm | n | oracle pass | root cause | wall s | turns | tokens (k) | cost USD |
|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 5 | 5/5 | 5/5 | 97 [80-112] | 22 [15-24] | 916 [818-1098] | 0.025 [0.023-0.030] |
| v2-01-config-overflow | C | 5 | 5/5 | 5/5 | 98 [75-124] | 33 [23-48] | 1230 [798-1264] | 0.030 [0.021-0.032] |
| v2-02-mp-deadlock | A | 5 | 5/5 | 5/5 | 192 [89-332] | 14 [10-17] | 511 [377-665] | 0.015 [0.013-0.019] |
| v2-02-mp-deadlock | C | 5 | 5/5 | 4/5 | 177 [169-219] | 45 [39-64] | 1333 [1135-1994] | 0.030 [0.026-0.038] |
| v2-03-resmgr-slog2 | A | 5 | 5/5 | 5/5 | 111 [62-269] | 17 [13-19] | 642 [337-665] | 0.018 [0.013-0.021] |
| v2-03-resmgr-slog2 | C | 5 | 5/5 | 5/5 | 88 [81-127] | 32 [32-43] | 1024 [864-1661] | 0.024 [0.023-0.036] |
| v2-04-deep-expr | A | 5 | 5/5 | 5/5 | 103 [68-156] | 18 [13-23] | 770 [586-1223] | 0.022 [0.017-0.030] |
| v2-04-deep-expr | C | 5 | 5/5 | 5/5 | 118 [91-142] | 40 [23-48] | 1271 [1053-1982] | 0.033 [0.025-0.051] |
| v2-05-hang-states | A | 5 | 5/5 | 5/5 | 213 [84-265] | 14 [11-22] | 560 [367-744] | 0.018 [0.015-0.021] |
| v2-05-hang-states | C | 5 | 5/5 | 5/5 | 122 [112-159] | 35 [28-37] | 990 [943-1224] | 0.028 [0.024-0.032] |
| v2-06-worker-null | A | 5 | 5/5 | 5/5 | 69 [60-103] | 16 [15-23] | 662 [637-1121] | 0.019 [0.018-0.026] |
| v2-06-worker-null | C | 5 | 5/5 | 5/5 | 70 [66-115] | 32 [20-45] | 840 [796-1368] | 0.021 [0.021-0.040] |
| v2-07-cmake-cross | A | 5 | 5/5 | 5/5 | 154 [126-166] | 42 [31-44] | 1485 [1078-1608] | 0.035 [0.028-0.038] |
| v2-07-cmake-cross | C | 5 | 5/5 | 5/5 | 138 [127-176] | 43 [37-49] | 1854 [1434-2235] | 0.038 [0.034-0.046] |
| v2-08-fd-leak | A | 5 | 5/5 | 5/5 | 77 [72-83] | 12 [12-17] | 441 [419-615] | 0.015 [0.013-0.016] |
| v2-08-fd-leak | C | 5 | 5/5 | 5/5 | 103 [86-132] | 33 [29-47] | 1082 [1000-1378] | 0.026 [0.024-0.033] |
| **all tasks** | A | 40 | 40/40 | 40/40 | 103 [60-332] | 16 [10-44] | 659 [337-1608] | 0.019 [0.013-0.038] |
| **all tasks** | C | 40 | 40/40 | 39/40 | 115 [66-219] | 37 [20-64] | 1185 [796-2235] | 0.028 [0.021-0.051] |
| **without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak** | A | 25 | 25/25 | 25/25 | 108 [60-265] | 21 [11-44] | 844 [367-1608] | 0.024 [0.015-0.038] |
| **without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak** | C | 25 | 25/25 | 25/25 | 115 [66-176] | 36 [20-49] | 1224 [796-2235] | 0.030 [0.021-0.051] |

### haiku: totals with exact 95% intervals

| tasks | arm | oracle pass | 95% interval | root cause | total cost USD | wall cap hits |
|---|---|---|---|---|---|---|
| all | A | 40/40 | 0.91-1.00 | 40/40 | 0.85 | 0 |
| all | C | 40/40 | 0.91-1.00 | 39/40 | 1.20 | 0 |
| without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak | A | 25/25 | 0.86-1.00 | 25/25 | 0.61 | 0 |
| without v2-02-mp-deadlock, v2-03-resmgr-slog2, v2-08-fd-leak | C | 25/25 | 0.86-1.00 | 25/25 | 0.78 | 0 |
| arm C without trials that ran a shell through remote_binary | C | 40/40 | 0.91-1.00 | 39/40 | 1.20 | 0 |

### haiku: paired (C minus A)

40 complete pairs: both pass 40, only A passes 0, only C passes 0, neither 0.
Median difference C minus A [range]: wall 4 [-188-130] s, turns 18 [-5-54], cost +0.008 [-0.005-+0.025] USD.

### haiku: how arm C worked

* MCP calls per trial: 22 [5-54]; trials with no MCP call: 0; ToolSearch calls per trial: 1 [1-2].
* MCP calls by tool (all 40 trials): file_get 186, deploy_and_run 157, file_put 116, build 99, processes 55, run_start 53, run_stop 44, target_status 40, file_list 36, logs 31, run_poll 30, get_crash 25, process_stop 14, target_list 9, target_start 1, target_stop 1.
* Agent ssh/scp/sftp calls refused by the guard: 0 in 0 trials; qnx-mcp ssh/scp calls 2365, hosts other than the trial target: 0.
* qnx-mcp errors returned to the agent: path_not_allowed 22, invalid_argument 19, process_not_allowed 13, timeout 5, build_failed 2.
* Programs run with remote_binary: /data/opt/acq/acqctl (6), /tmp/sv/stress (5), /data/opt/status/statusctl (4), /tmp/statusverify-driver (4), /tmp/statusverify-ctl-fixed (4), /tmp/drv (4), /tmp/sv/statusctl-test (4), /tmp/statusdbg/statusctl (2), /tmp/acqfix-acqctl (2), /tmp/gwt-fixed/feed (2), /tmp/statusverify-orig (1), /tmp/statusverify-fixed (1), /tmp/acqfix-acqd (1), /tmp/statusd_orig (1), /tmp/statusd_fixed (1), /tmp/sv/statusd-buggy (1), /tmp/sv/statusd-fixed (1), /tmp/gwfix/storaged (1), /tmp/gwfix/gatewayd (1), /tmp/gwt-base/storaged (1), /tmp/gwt-base/gatewayd (1), /tmp/gwt-base/feed (1), /tmp/gwt-fixed/storaged (1), /tmp/gwt-fixed/gatewayd (1); trials that ran a shell this way: 0, a system utility: 0.

### haiku: transcript and guard flags (all arms, for review)

* arm A: file access outside workspace (2), host-destructive (1), other host or target directory (2), review: another target stopped or started during the session (1), review: disassembly (check whose binary) (1), ssh/scp to another host refused by the guard (1)
* arm C: review: another target stopped or started during the session (1)

Agent cost 15.31 USD (Claude Code list-price estimate), judge 0.90 USD.

## Pilot gaps and limitations seen in the study

* `process_stop` refuses processes whose executable path is relative. The field services are started as `cd /data/opt/<dir> && ./<prog> &`, so their path is `./gatewayd` and the like, and qnx-mcp answers `process_not_allowed: ... is not a plain absolute path`. This happened in 14 of 40 Sonnet and 9 of 40 Haiku arm C trials (v2-02, v2-03, v2-08, once v2-05). In the pilot the agents then checked their fix with their own copy of the service started through `run_start` and left the installed one running ([pilot-gaps.md](pilot-gaps.md)); in the study every one of these trials passed. A shell user stops these services with `slay` or `kill`. qnx-mcp was not changed during the study.
* `QNX_MCP_WORKSPACE` (set by the harness to the trial workspace) refused host paths outside the workspace 18 times (Sonnet) and 22 times (Haiku), with `path_not_allowed` in `build`, `file_put`, `file_get`, `run_start` and `deploy_and_run`; every one of those trials passed.
* On v2-02, where the hung client is REPLY-blocked and a signal does not end it, `deploy_and_run` returned a `timeout` error after 53 to 65 s instead of an outcome (5 times per model).

## How arm C worked

* Every arm C trial loaded the qnx-mcp tools (one or two ToolSearch calls) and used them: Sonnet median 10 MCP calls [4-31], Haiku 22 [5-54]. In its 3 phase 2 arm B trials, with SSH available, Haiku never loaded them; without SSH it always did.
* No arm C agent called ssh, scp or sftp (the agent's guard logged no call in 80 trials), and the transcript scan found no other attempt to reach the target. qnx-mcp's own 1278 (Sonnet) and 2365 (Haiku) ssh and scp calls all went to the trial's target.
* `remote_binary` was used only for programs under `/data/opt` and `/tmp` (the task's installed clients and the agents' own copied builds). No agent ran a shell or a system utility through it, although qnx-mcp v0.2.1 would allow that.

## Flags reviewed by hand (all in arm A or outside the agents)

* `pkill` inside the command that ssh ran on the trial target (2), flagged as host-destructive: scan false positive.
* `objdump -d src/expr.o` on the agent's own object file (2): allowed by the prompt; no SDK binary was inspected.
* One Haiku arm A agent tried twice to edit `~/work/qnx-mcp-eval/skills/qnx-platform-facts/SKILL.md`, outside its workspace; both edits failed because the file does not exist, and nothing was created there.
* One scp refused by the guard because the agent passed unquoted `uname -a` output as its destination: a malformed command, not another host.
* Two QEMU processes that are not trial targets started during a session (one arm A, one arm C; their directory could not be read).

## Scoring notes

* The two root-cause misses: Sonnet arm A v2-04 (eae83a6) enlarged the thread stack, which the judge rates as masking the unbounded recursion; the oracle still passed, so the v2-04 oracle accepts that fix. Haiku arm C v2-02 (e4d25ed): the judge returned no verdict (no parsable JSON), although its note says the mechanism and fix are right; it is counted as not right, as recorded.
* Two Sonnet arm A trials of v2-03 (e28034a, ee6f0f0) took 746 and 742 s; Claude Code reported 2 turns for each although they made 8 and 9 tool calls.

## What this shows and what it does not

It shows that on these eight seeded QNX 8.0 bugs, which need evidence from the target (its configuration, logs, thread states, cores or libraries), both models fixed every bug without any shell on the target, using only qnx-mcp's tools: 80 of 80 arm C trials against 80 of 80 with a root shell, with no attempt to get a shell. The price of least privilege here was more turns and tokens (about twice the turns), a little more time, and 61 percent (Sonnet) and 47 percent (Haiku) more cost per trial at the median.

It does not show that qnx-mcp makes agents more successful: both arms are at the ceiling, so this task set cannot detect a difference in success, and 40 of 40 only bounds each pass rate above 0.91 (95%). It is not a security evaluation: the agent ran as the same Unix user with Bash on the build host and the box's default SSH key, so isolation rested on a PATH guard and transcript review, not on the operating system, and `remote_binary` can run any installed program as root. The tasks are our own small programs on QEMU, with one client (Claude Code 2.1.312) and two models; costs are Claude Code's list-price estimates; QEMU timing is not real-time evidence.
