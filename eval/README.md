# qnx-mcp-eval (copy in the qnx-mcp repository)

This directory is a copy of the qnx-mcp-eval repository at commit 83925d9 (4 November 2026): the preregistered protocol ([PROTOCOL.md](PROTOCOL.md)), the tasks with their hidden parts (oracles, reference fixes, ground truth, unseen inputs), the harness, the black-box probes in `evidence/`, every results summary (Markdown and JSON), and in `results/raw.tar.xz` (5.9 MB, 55 MiB unpacked) the 253 per-trial directories (prompt, transcript, diff, oracle log, metrics, guard log) with the other raw logs. Run `tar -xJf results/raw.tar.xz` in this directory to restore them at the paths the summaries link to. Before the copy was added, `python3 -I harness/scan_secrets.py` was run over it and over the unpacked archive: the only finding is the public address licensing@qnx.com, quoted in one transcript; no keys, tokens, AWS account ids or host names, and no IP addresses outside libvirt's private 192.168.122.0/24. Two symlinks of task v2-08 are listed in `tasks/v2-08-fd-leak/hidden/SYMLINKS.txt` instead of stored as links; recreate them before running that task. The original README follows.

A reproducible comparison of two ways to give a coding agent QNX abilities: QNX's own Markdown agent skills ([qnx/qnx-agent-skills](https://github.com/qnx/qnx-agent-skills)) with plain SSH, against the same setup plus the [qnx-mcp](https://github.com/NikhilGogu/qnx-mcp) server. Phase 3 asks a least-privilege question: can an agent with qnx-mcp and no shell on the target fix the same bugs as an agent with a root shell? The agent is Claude Code on Amazon Bedrock; the targets are QNX OS 8.0 x86_64 QEMU virtual machines built with mkqnximage.

The design is preregistered in [PROTOCOL.md](PROTOCOL.md).

## Results so far (phase 1, 3 November 2026)

This is a pilot of 2 trials per task and arm. It tests the harness and measures cost; it is not evidence for or against qnx-mcp.

* Task validation (`eval.py validate`, [results/validation](results/validation)): on QNX 8.0 x86_64 QEMU, all eight seeded bugs fail their oracle (exit 139 and 134 for the crashes, link errors, hangs past the time limit, wrong outputs and a wrong devctl status) and all eight reference fixes pass.
* Pilot (t01-calib-null and t05-msgreply, arms A and B, 2 trials each, [results/pilot-2026-11-03/summary.md](results/pilot-2026-11-03/summary.md)): 8 of 8 trials passed the oracle, 8 of 8 named the right root cause (blind judge) and 8 of 8 changed the ground-truth lines. The transcript scan flagged no unsafe or disallowed action. Median agent time 21.6 s in arm A and 36.2 s in arm B, median turns 7 and 11.5, median cost 0.07 and 0.10 USD. Arm B called qnx-mcp tools in both t01 trials (6 and 7 calls: target_status, build, deploy_and_run, get_crash) and in neither t05 trial; in one t05 arm B trial the agent ran the hanging client over SSH and waited for the Bash tool's 2 minute limit (151 s in total).
* Difficulty screen (arm A only, one trial of each of the other six tasks, [results/screen-2026-11-03/summary.md](results/screen-2026-11-03/summary.md)): 6 of 6 passed, in 22 to 123 s, at 0.07 to 0.10 USD each.

What this shows: with Claude Sonnet 5.5 and these small programs whose bug can be found by reading the source, both arms are at the ceiling. In all 14 trials the agent read the task's C sources before its first command on the target, and in at least 4 it wrote the fix before running anything on the target (`python3 harness/order_check.py results/pilot-2026-11-03 results/screen-2026-11-03`, output in [results/order-check-2026-11-03.txt](results/order-check-2026-11-03.txt)). This task set cannot show a difference in success; on t01 the MCP arm was slower and more expensive because it reproduced the crash with the tools before fixing it.

Cost and duration per trial (14 trials): agent cost 0.067 to 0.123 USD (mean 0.084), agent time 20 to 151 s (mean 44), and 62 to 222 s per trial from target boot to finished scoring, with four trials at a time. The blind judge costs about 0.004 USD per trial.

## Results, phase 2 (v2 tasks, 3 November 2026)

Eight hardened tasks whose fault needs evidence from the target. Arm A screening with Sonnet 5.5 passed 16/16 in both rounds, so every task is at the ceiling ([results/screen-v2-a](results/screen-v2-a), [results/screen-v2-b](results/screen-v2-b)). The A and B pilot stopped part way (Sonnet 28 of 32 trials, Haiku 6 of 32, the rest skipped by the budget guard): every trial that ran passed, and Sonnet used qnx-mcp only on the crash tasks ([results/pilot-v2.md](results/pilot-v2.md)).

## Results, phase 3: least privilege (3 and 4 November 2026)

Arm A (root shell over SSH) against arm C (qnx-mcp v0.2.1 only; the agent's ssh, scp and sftp refuse the target), the eight v2 tasks, 5 trials per task, arm and model, 160 trials, all scored ([summary](results/least-privilege-2026-11-03/summary.md)):

| model | arm A oracle pass | arm C oracle pass | median cost A / C (USD) | median turns A / C |
|---|---|---|---|---|
| Sonnet 5.5 | 40/40 | 40/40 | 0.123 / 0.198 | 10 / 20 |
| Haiku 5.5 | 40/40 | 40/40 | 0.019 / 0.028 | 16 / 37 |

No arm C agent tried ssh, scp or sftp, and qnx-mcp's own ssh calls reached only the trial target. Without a shell the agents needed about twice the turns and paid 47 to 61 percent more per trial at the median. `process_stop` refused services started with a relative path (23 of 80 arm C trials); those trials still passed. Both arms are at the ceiling, so this shows that the shell-free setup was enough for these tasks, not that it is better; isolation rested on a PATH guard and transcript review, not on the operating system.

## Layout

```
PROTOCOL.md            preregistered design, metrics, analysis plan, AWS resources
tasks/<id>/task.json   the user's bug report (goes into the prompt) and task metadata
tasks/<id>/task/       the program the agent sees (C source, Makefile, README)
tasks/<id>/hidden/     oracle, reference fix, ground truth, unseen inputs (never on the box during a trial)
harness/eval.py        controller: sync, validate, trial, pilot, summarize, smoke
harness/box/           scripts that run on the build box (targets, workspace, agent run, oracle helpers)
harness/box/skills*/   qnx-mcp skill files: skills/ for arm B (phase 2), skills-c/ for arm C (v0.2.1)
harness/lp_report.py   phase 3 tables (pass counts, medians with ranges, intervals, paired differences, arm C tool use)
results/validation/    oracle logs: each seeded bug fails, each reference fix passes
results/<run>/         per-trial transcript, diff, oracle log, metrics; summary.md
evidence/              Bedrock check and the black-box QNX probes used to design the tasks
```

## Requirements

* A build host with QNX SDP 8.0, QEMU with KVM and libvirt's default network (`virbr0`), reachable over SSH as `qnx` from the controller.
* On the build host: `~/eval-targets/golden`, a directory built once with `mkqnximage --type=qemu --arch=x86_64 --ssh-ident=$HOME/.ssh/id_ed25519.pub --noprompt`; the Claude Code binary at the path in `harness/config.json`; a frozen qnx-mcp checkout with its own venv at `~/work/qnx-mcp-eval/server-snapshot`; the pinned skills workspace at `~/work/qnx-mcp-eval/runtime/qnx-agent-skills`.
* Bedrock access for the model in `harness/config.json` on the build host (an instance role) and on the controller (for the blind judge).

## Commands

```sh
python3 harness/eval.py validate                    # all tasks: oracle fails on the bug, passes with the fix
python3 harness/eval.py smoke A                     # cheap end-to-end check of the agent pipeline (B: with qnx-mcp)
python3 harness/eval.py trial t01-calib-null B 1    # one trial, fully scored
python3 harness/eval.py pilot --name pilot-2026-11-03 --tasks t01-calib-null t05-msgreply --reps 2
python3 harness/eval.py summarize results/pilot-2026-11-03
python3 harness/eval.py snapshot-server 249d08a       # qnx-mcp clone with its own venv on the box (arms B and C)
python3 harness/eval.py smoke C                     # arm C: qnx-mcp only, ssh to the target refused
python3 harness/eval.py pilot --name least-privilege-2026-11-03/study --tasks v2-01-config-overflow ... --reps 5 --arms AC --models sonnet haiku --seed 20261107
python3 harness/lp_report.py results/least-privilege-2026-11-03/study
```

## Licence

Apache-2.0, see [LICENSE](LICENSE). The task programs, oracles and harness are our own code; no QNX binaries were inspected to build them.
