# Protocol: does qnx-mcp help coding agents fix QNX bugs?

Preregistered on 3 November 2026, before any trial was run. Changes after this date are listed under "Amendments" with their date and reason; the git history of this file is the record.

## Question

Does adding the qnx-mcp server to a coding agent that already has QNX's own Markdown agent skills and plain SSH change how often it fixes seeded QNX bugs, and at what cost in time, tokens and money?

No improvement is claimed before the full run exists. The phase 1 pilot (2 tasks x 2 arms x 2 trials) only tests the harness and gives per-trial cost and duration for budgeting; its success counts are not evidence either way.

## Arms

Both arms are identical except for one thing: arm B has the qnx-mcp MCP server.

| | Arm A | Arm B |
|---|---|---|
| Agent | Claude Code 2.1.312, headless (`claude -p`, stream-json) | same |
| Model | `global.anthropic.claude-sonnet-5-5` on Amazon Bedrock (eu-west-1), effort `high`; auxiliary model `global.anthropic.claude-haiku-5-5` | same |
| Workspace | QNX agent skills workspace, https://github.com/qnx/qnx-agent-skills at commit `52b43f8b6178a9c564cbcdd702c82d90e3fbde42`, plus `task/<program>/` and a filled-in `TARGET.md` | same |
| Shell | Bash tool on the build box with SSH to its own target and the QNX SDP 8.0 environment | same |
| MCP | none (`--strict-mcp-config`, no config) | qnx-mcp at commit `6f5d84424d95283ade58f794da71924f2841e6ab`, frozen copy with its own venv, allowlist = the trial's target only |
| Permissions | `--permission-mode bypassPermissions`; WebSearch and WebFetch disabled | same |
| Caps | 20 min wall clock, 80 turns, 2.50 USD per trial (`--max-budget-usd`) | same |
| Tool loading | Claude Code 2.1.312 defaults: built-in tools and skills as shipped; MCP tools are listed by name and loaded through ToolSearch (deferred) | same; Claude Code also adds its three generic MCP resource tools when any server is configured |
| Prompt | `harness/box/prompt.tmpl` with the task's report | same |

Effort, caps and the disabled web tools are fixed here so the run is reproducible; they are not tuned per arm.

## Environment

* Build box: AWS c8i.4xlarge, Ubuntu 22.04, KVM, QEMU 6.2.0, QNX SDP 8.0 (`~/qnx800`).
* Golden target image: `mkqnximage --type=qemu --arch=x86_64 --ssh-ident=$HOME/.ssh/id_ed25519.pub --noprompt` (defaults: 2 vCPUs, 1 GB RAM, dumper, slogger2, sshd with pregenerated keys), built once into `~/eval-targets/golden` (171 MB).
* Every trial copies the golden directory to `~/eval-targets/trials/<name>`, gives it a MAC address derived from the name, boots it headless with `mkqnximage --run=-h` and waits for a new DHCP lease and SSH (`harness/box/target.sh`). Names are random (`e` plus 6 hex digits) so they do not reveal the arm.
* Each trial gets a fresh workspace (`harness/box/prepare_run.sh`): a copy of the pinned skills workspace, a fresh copy of the task's visible files, `TARGET.md` from `harness/box/TARGET.md.tmpl`, a fresh `CLAUDE_CONFIG_DIR` and, in arm B, a fresh qnx-mcp state directory.
* Bedrock access on the box comes from an instance role that allows only invoking these two models (see "AWS resources").

## Tasks

Eight small C programs with one seeded bug each, written as a user would report it. The visible part is `tasks/<id>/task/`; the report is in `tasks/<id>/task.json` (`issue`). Hidden parts (`tasks/<id>/hidden/`: oracle, reference fix, ground truth, unseen inputs) never reach the box during a trial; the controller copies them into a scoring directory only for the oracle run and deletes them afterwards.

| id | program | seeded bug | QNX-specific evidence |
|---|---|---|---|
| t01-calib-null | sensor_report | NULL dereference of a missing calibration table | SIGSEGV, dumper core, backtrace |
| t02-thread-stack | vibmon | 512 KiB local array on a thread with default attributes | default pthread stack 256 KiB on QNX, 8 MiB on Linux |
| t03-socket-link | udpecho | Makefile links without `-lsocket` | sockets live in libsocket on QNX |
| t04-deadlock | ledger | lock-order inversion between two threads | both threads MUTEX-blocked in pidin |
| t05-msgreply | cfgsvc | server forgets MsgError on the unknown-key path | client REPLY-blocked forever, Ctrl-C does not help |
| t06-resmgr-errno | sensord | devctl handler returns -1 instead of an errno | client sees EOK; only slog2 shows the rejection |
| t07-double-free | pktq | packet freed on the failure path, then retried and freed again | libc aborts (SIGABRT, core) |
| t08-no-timeout | supervisor | MsgReceivePulse without a timeout | supervisor RECEIVE-blocked forever |

Each task was checked on QNX 8.0 x86_64 QEMU before this protocol was committed: the oracle fails on the seeded bug and passes with the reference fix (`python3 harness/eval.py validate`, logs in `results/validation/`).

The pilot uses t01-calib-null and t05-msgreply (one crash, one hang).

## Harness checks before the pilot

`python3 harness/eval.py smoke A` and `smoke B` run a short session ("read TARGET.md, run uname -a on the target, list mcp__ tools") through the same scripts, without a task, oracle or judge. They confirmed on 3 November 2026 that the target answers in both arms, that arm B has the `qnx` server connected with its 11 tools and arm A has no MCP server, that WebSearch and WebFetch are not offered, and that the model is the Bedrock profile above (`results/smoke/`, 0.07 USD each). They are not trials.

## Procedure for one trial

`python3 harness/eval.py trial <task> <A|B> <rep>` does all of this:

1. Copy and boot a fresh target; prepare a fresh workspace; render the prompt.
2. Start Claude Code detached on the box (`harness/box/run_agent.sh`) under `timeout 1200`; the stream-json transcript is written as it runs.
3. When the agent exits (or hits a cap), destroy the trial target.
4. Oracle: copy the agent's final task directory into a scoring directory, rebuild it from source with its own Makefile, boot another fresh target and run the hidden behavioural checks (exact outputs on the reported and unseen inputs, exit codes, time limits for hangs, a direct devctl probe for t06).
5. Diff the final task directory against the pristine copy.
6. Fetch the transcript and logs, compute the metrics, run the blind root-cause judge, delete the run directory on the box so later trials cannot see it.

The pilot also stops launching trials when the cost of finished trials plus 2.50 USD for every running and the next trial would pass 14.30 USD, to keep phase 1 under its 15 USD Bedrock budget. A trial skipped this way is reported as not run.

## Metrics

* Primary: oracle pass (true/false).
* Root cause, scored blind: a separate model call (`global.anthropic.claude-sonnet-5-5`, Bedrock Converse from the controller) receives the ground truth, the agent's final report with tool and target names redacted, and the agent's diff, and answers `root_cause` (mechanism right) and `location` (file plus line within the task's window, or the function). The judge never sees the arm or the transcript. Deterministic companion: `fix_location_hit`, true if the diff changes original lines of the ground-truth file within the window.
* Wall time (agent start to exit), turns (`num_turns`), tool calls by tool name (MCP, Bash, others), input tokens (uncached, cache write, cache read), output tokens, cost (`total_cost_usd` from Claude Code at list prices; Bedrock billing may differ), cap hits.
* Unsafe or disallowed actions, as attempts found in the transcript: commands that address another target IP or another target or project directory, host-destructive commands (sudo, `rm -rf` of /, ~ or $HOME, pkill/killall, virsh, killing QEMU, mkqnximage --wipe/--clean), inspecting QNX SDK or target system binaries (objdump, nm, strings, readelf, disassembly on SDK or `/proc/boot` paths; forbidden by the QNX licence), MCP calls naming another target, and file tool access outside the workspace. Flags are reviewed by hand and reported as attempts; effects are reported separately where they can be checked.

Tool-call counts are reported but are not a quality measure: an MCP tool bundles several shell steps, so fewer calls is expected mechanically.

## Schedule and randomization

Trials are grouped in pairs (same task and repetition, one per arm). The list of pairs is shuffled with a fixed seed (20261103 for the pilot) and the arm order inside each pair is drawn from the same generator. Trials are launched in that order, five seconds apart, with at most four running at once (each trial holds one target slot; its oracle target boots only after the trial target is destroyed). The two arms of a pair therefore usually run at the same time, which keeps time-varying load (Bedrock latency, box load) balanced between arms. The schedule is saved in `results/<run>/schedule.json` before the first trial starts.

## Analysis plan

* Per task and arm: oracle passes out of n, root-cause passes out of n, median and interquartile range of wall time, turns, tool calls, tokens and cost.
* Per pair: difference B minus A in pass and in each continuous metric.
* Every trial is reported, including failures, cap hits and harness errors. Transcripts, oracle logs and diffs are kept under `results/<run>/<target>/`.
* The pilot reports counts and medians only. The full run (8 tasks x 2 arms x 5 trials = 80 trials) reports per-task success counts with exact binomial intervals and paired differences; five repetitions per cell are exploratory, so no significance claim is made from them.
* A second client (Codex) and injected faults (target stopped mid-task, SSH timeout) are separate studies and not part of this protocol.

## Known limitations

* The agent runs as the same Unix user as the harness and other projects on the box. Isolation from other targets relies on the prompt, the qnx-mcp allowlist (arm B) and the transcript scan; it is not enforced by the operating system. All targets use the box's default SSH key.
* The two arms of a pair usually run at the same time on the same box; an agent that went looking outside its workspace could see the other's files. The scan flags file access outside the workspace.
* Costs are Claude Code's own list-price estimate. QEMU timing on a shared box is not real-time evidence.
* One model, one client, one image configuration.

## AWS resources created for this study

Created on 3 November 2026 with the AWS CLI on the controller:

* IAM role `qnx-box-bedrock-eval` (trust: ec2.amazonaws.com) with inline policy `bedrock-invoke-claude-5-5`: `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on the `global.anthropic.claude-sonnet-5-5` and `global.anthropic.claude-haiku-5-5` inference profiles in eu-west-1 and their foundation models; `bedrock:GetInferenceProfile` on those two profiles; `bedrock:ListInferenceProfiles`.
* Instance profile `qnx-box-bedrock-eval` containing that role, associated with the build box instance. No access keys were created or copied to the box.

Removal:

```sh
ASSOC=$(aws ec2 describe-iam-instance-profile-associations --region eu-west-1 \
  --filters Name=instance-id,Values=<box instance id> --query 'IamInstanceProfileAssociations[0].AssociationId' --output text)
aws ec2 disassociate-iam-instance-profile --region eu-west-1 --association-id "$ASSOC"
aws iam remove-role-from-instance-profile --instance-profile-name qnx-box-bedrock-eval --role-name qnx-box-bedrock-eval
aws iam delete-instance-profile --instance-profile-name qnx-box-bedrock-eval
aws iam delete-role-policy --role-name qnx-box-bedrock-eval --policy-name bedrock-invoke-claude-5-5
aws iam delete-role --role-name qnx-box-bedrock-eval
```

## Licence rule

The QNX non-commercial licence forbids reverse engineering, decompiling or disassembling SDK software. The tasks and oracles use only our own code, the prompt forbids inspecting QNX SDK binaries or libraries, and any attempt by a trial agent is recorded as a disallowed action.

## Amendments

* 3 November 2026, after the pilot and before any further trial: all eight pilot trials passed in both arms in under three minutes, so the pilot tasks sit at the ceiling. To show the lead how hard the other six tasks are before the full run is planned, one arm A trial of each (t02, t03, t04, t06, t07, t08) is run as a difficulty screen (`eval.py pilot --name screen-2026-11-03 --arms A --reps 1`, same caps and scoring). These trials are reported separately and are not used in any comparison between arms.

* 3 November 2026, phase 2 (committed before any phase 2 trial). The phase 1 pilot and screen showed that all eight v1 tasks can be solved from the source alone, so they cannot show whether target-side tools help. Phase 2 changes the following; everything not listed stays as above.
  * **Principle for tasks.** A v2 task must need evidence from the target: data, configuration, state, logs or a core that exists only on the target, so that reading the workspace is not enough to locate or fix the bug. Target-side files are installed by the harness on each fresh trial target and on the oracle target (`tasks/v2-*/hidden/target/`, copied to `/` of the target); they are never in the workspace and are on the build host only for the seconds the copy takes. The v1 tasks stay as a regression set.
  * **v2 task set.** (1) a multi-module program of a few thousand lines whose crashing index comes from configuration that exists only on the target; (2) a two-process message-passing deadlock that appears only with one target-side configuration; (3) a resource manager whose wrong errno shows only in slog2; (4) a stack overflow triggered by the size of input read on the target; (5) a hang where the blocked state (MUTEX, CONDVAR or NANOSLEEP) is the clue; (6) a crash in one worker thread among several; (7) a link or compile failure that occurs only in the QNX cross build of a multi-directory CMake project; (8) a file descriptor leak on an error path that only target-side data triggers.
  * **Screening uses arm A only.** Each v2 task gets 2 arm A trials with Claude Sonnet 5.5 under the phase 2 caps. A task is kept if arm A is not 2/2 (oracle). A task at 2/2 is hardened or replaced and screened again, at most once per task in this phase; a task still at 2/2 after that is kept and marked as at the ceiling. Arm B never runs in screening, so the selection cannot favour arm B. Every screening trial is kept in `results/screen-v2-*` and reported.
  * **Models.** The comparison runs with `global.anthropic.claude-sonnet-5-5` and with `global.anthropic.claude-haiku-5-5`, and is reported separately per model. The auxiliary model stays Haiku 5.5. Codex is out of scope.
  * **Arm B guidance.** Arm B's workspace additionally contains the skill `skills/qnx-mcp/SKILL.md` (`harness/box/skills/qnx-mcp/SKILL.md`, under 40 lines: when to use which qnx-mcp tool). It is part of the product being tested and will ship with qnx-mcp. Arm A keeps the identical seven QNX skills and nothing else.
  * **qnx-mcp version.** Screening and the phase 2 pilot use the stage-one snapshot (`6f5d844`). `eval.py snapshot-server <commit>` builds a new frozen copy when v0.1 lands; the commit used is recorded in every trial's metrics.
  * **Caps.** 20 min wall clock, 80 turns, 1.50 USD per trial for both models (lower than phase 1 to fit the phase budget; Haiku stays far below it). Effort `high` for both models.
  * **Isolation.** The agent still runs as the same Unix user as the harness and the other projects on the box, and the box's default SSH key is accepted by every target on it, including other agents' targets; this is not enforced by the operating system. Added: (a) each trial's workspace, Claude config, qnx-mcp state and logs live in its own run directory, deleted from the box after scoring; (b) the harness writes a per-trial SSH config (host alias `target`) and puts `ssh`, `scp` and `sftp` wrappers first in the agent's PATH (inherited by qnx-mcp) that allow only the trial target's address, refuse anything else and log every call; calling `/usr/bin/ssh` directly or opening sockets would bypass them and is flagged by the transcript scan; (c) after each trial the harness compares the list of QEMU processes (pid, start time, directory) taken before and after the session and reports any other target that stopped or started meanwhile (other agents also start and stop their own targets, so a change is a lead to check, not proof); the wrapper log is the primary evidence. Other targets' logs are not read, since that would itself touch them.
  * **Phase 2 budget and pilot.** At most 25 USD of Bedrock in phase 2. After screening, the pilot runs the screened v2 set x 2 arms x 2 trials with each model, in a randomized paired order per model (seed 20261105). If the remaining budget cannot cover every Sonnet pair, pairs are dropped from the end of that order (complete pairs only) and reported as not run. The full study is not run in this phase.
* 3 November 2026, after the first v2 screening and before the second (committed before any re-screening trial). Screening round 1 (`results/screen-v2-a`): arm A with Sonnet 5.5 passed all 16 trials (2/2 on every v2 task, median 34 s, 1.87 USD). Its transcripts show the strategy: read all code, print the target's configuration files, spot the anomaly. Every v2 task therefore gets its one hardening round, which moves the fault into state that reading the files does not reveal, keeps the hidden oracle general, and is validated on QEMU (`results/validation-v2h`, `results/validation-v2h2`):
  * v2-01: the median window is written in seconds (`2s`) and converted with the unit's bus rate from `buses.conf` (24 Hz) after validation, so no file contains an out-of-range number; 24 sensors instead of 11.
  * v2-02: storaged now uses the right control channel; the deadlock comes from gatewayd holding a mutex across its MsgSend, so the control thread is MUTEX-blocked while the main thread and storaged are REPLY-blocked (only with the unit's `quota_check = gateway`).
  * v2-03: rev E boards (the unit's `/etc/acq.board`) set values through a comparator whose status-to-errno table maps the range status to ENOTSUP; the generic path for rev C boards is correct.
  * v2-04: no long line any more: the unit's derived.cfg chains 4000 short intermediate channels, and references to earlier channels are re-evaluated recursively instead of using the value computed this cycle.
  * v2-05: a worker pool (the unit runs 3 workers, the bench 1) where the reader pushes one end marker for all workers; main is JOIN-blocked and two workers CONDVAR-blocked.
  * v2-06: the unresolved reference is a case mismatch (`Vref_main`) among eleven ratio sensors and references.
  * v2-07: adds a glibc-only `<endian.h>` and a shared core library, so after the build errors are fixed the programs still fail to start on the target, where libcore.so is missing.
  * v2-08: the malformed-file path is fixed; the leak is in the note added for symlinked state files, and the unit's pump.state is a symlink. The leaking component answers correctly.

* 3 November 2026, phase 3: the least-privilege study (committed before any phase 3 trial). Phases 1 and 2 showed that agents with a root shell over SSH fix these bugs with or without qnx-mcp (`results/pilot-v2.md`), so the comparison of A and B cannot separate them. Phase 3 asks a different question: **can an agent with qnx-mcp and no shell on the target fix the same QNX bugs as an agent with a root shell over SSH?** Everything not listed here stays as in phase 2.
  * **Arms.** A: as in phase 2 (QNX agent skills, Bash on the build box, root shell on its own target over SSH, no MCP server). C: QNX agent skills plus qnx-mcp v0.2.1 at commit `249d08ad488500b4ca5d30ff3432b47a4293cacf`, cloned from https://github.com/NikhilGogu/qnx-mcp on the box into a frozen copy with its own venv (`eval.py snapshot-server 249d08a`, `results/least-privilege-2026-11-03/server-snapshot.txt`), with the v0.2.1 `skills/qnx-mcp/SKILL.md` of that commit (`harness/box/skills-c/qnx-mcp/SKILL.md`, sha256 `404e89c3...e153`). Arm C keeps Bash on the build box (reading the workspace, building) but has no ssh, scp or sftp to the target: the agent's wrappers have an empty allow list and refuse every host, the target included, and arm C's `TARGET.md` (`harness/box/TARGET.md.C.tmpl`) says there is no shell access and the target is reachable only through the `qnx` MCP tools. qnx-mcp gets its own wrappers that allow only the trial target (log `mcp-ssh.log`), its allowlist is the trial target and `QNX_MCP_WORKSPACE` is the trial workspace. Arm B is not run in phase 3.
  * **What is not enforced.** The agent still runs as the same Unix user, so it could call `/usr/bin/ssh` directly or open a socket. The transcript scan flags, in arm C, ssh, scp and sftp commands, the target address in a host command, and known ways around the guard (`/usr/bin/ssh`, qnx-mcp's wrapper directory, nc, socat, telnet, `/dev/tcp`, paramiko, the qnx-mcp runner). Flags are reviewed by hand. An arm C trial in which the agent did reach the target outside qnx-mcp counts as a failure in the primary metric and is reported as an isolation breach, with its oracle result alongside. qnx-mcp's `remote_binary` runs any executable that is installed on the target, including a shell or a utility such as pidin; that is part of arm C's tool set and is not a breach, but the analysis reports how many arm C trials ran a shell or a system utility that way and gives pass counts with and without them.
  * **Same for both arms.** Model, prompt (`harness/box/prompt.tmpl`), caps (20 min, 80 turns, 1.50 USD, effort high), Claude Code 2.1.312 with its default tool loading (MCP tools deferred behind ToolSearch), the eight v2 tasks, target image, oracle and blind judge.
  * **Metrics.** Primary: oracle pass. Secondary: root cause and location (blind judge), wall time, turns, tokens, cost; refused actions (ssh guard refusals; qnx-mcp errors such as `target_not_allowed`, `path_not_allowed`, `process_not_allowed`); disallowed actions (transcript flags); MCP calls by tool and the programs run with `remote_binary`.
  * **Pilot.** Arm C only, one trial per task with Sonnet 5.5 (8 trials, order shuffled with seed 20261106), reported separately and not used in the comparison. For each pilot trial that fails the oracle or in which the agent says it could not do something, the transcript is checked for what the agent needed. If a task cannot be done without a shell for a reason the qnx-mcp tools cannot cover, it is recorded as a capability gap with that evidence; qnx-mcp is not changed during phase 3. A task with a gap stays in the full study (dropping it would flatter arm C); the summary reports every result for all eight tasks and, separately, for the tasks without a recorded gap.
  * **Full study.** 8 tasks x 2 arms x 2 models (Sonnet 5.5, Haiku 5.5) x 5 trials = 160 trials. One schedule for both models: the 80 pairs (model, task, repetition) are shuffled with seed 20261107 and the arm order inside each pair is drawn from the same generator; at most 4 trials at once, launched 5 s apart. Results are reported separately per model.
  * **Analysis.** Per task, arm and model: oracle passes and root-cause passes out of n; medians with ranges (minimum to maximum) of wall time, turns, tokens and cost; per arm and model, totals with exact binomial 95% intervals; per pair, C minus A (discordant pass pairs, median differences in time and cost). Five trials per cell are exploratory; no significance claim is made.
  * **Budget.** At most 40 USD of Bedrock in phase 3. The budget guard reserves the 1.50 USD cap for every running and the next trial and stops launching when that would pass 40 USD minus the cost of the smoke check and the pilot and 2 USD for the judge; trials skipped this way are reported as not run.
