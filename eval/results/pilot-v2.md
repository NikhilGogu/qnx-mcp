# Phase 2 pilot (v2 tasks, arms A and B), as it stopped

Runs: `eval.py pilot --name pilot-v2-sonnet --model sonnet` and `--name pilot-v2-haiku --model haiku`, the eight hardened v2 tasks x 2 arms x 2 trials in the randomized paired order of seed 20261105 (`schedule.json` in each directory). Every trial that ran was scored (oracle, blind judge, metrics). Tables: `python3 harness/compare.py results/pilot-v2-sonnet results/pilot-v2-haiku` (below) and each directory's `summary.md`.

Completeness. Sonnet 5.5: 28 of 32 scheduled trials ran; the last two pairs in the order (v2-07-cmake-cross rep 1 and v2-03-resmgr-slog2 rep 1) were skipped by the pilot's budget guard. Haiku 5.5: 6 of 32 ran (v2-01 rep 1 and v2-08 rep 2 at positions 1 and 2 of the order, v2-03 rep 1 at position 16); the other 26 were skipped by the budget guard within two minutes of the start, because the guard reserves the 1.50 USD per-trial cap for every running trial while a Haiku trial costs about 0.02 USD. The skips are in the controller log (`skip <target>: budget guard`). No trial was rerun; these results are as the run left them.


### pilot-v2-sonnet (sonnet, 28 scored trials)

| task | A pass | B pass | A root cause | B root cause | A median s | B median s | A median USD | B median USD | B MCP calls (per trial) |
|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | 2/2 | 2/2 | 2/2 | 2/2 | 38.1 | 51.7 | 0.12 | 0.16 | 6, 4 |
| v2-02-mp-deadlock | 2/2 | 2/2 | 2/2 | 2/2 | 32.85 | 31.6 | 0.1 | 0.1 | 0, 0 |
| v2-03-resmgr-slog2 | 1/1 | 1/1 | 1/1 | 1/1 | 35.2 | 166.4 | 0.11 | 0.14 | 0 |
| v2-04-deep-expr | 2/2 | 2/2 | 2/2 | 2/2 | 37.1 | 40.95 | 0.12 | 0.14 | 4, 6 |
| v2-05-hang-states | 2/2 | 2/2 | 2/2 | 2/2 | 39.35 | 29.55 | 0.09 | 0.09 | 0, 0 |
| v2-06-worker-null | 2/2 | 2/2 | 2/2 | 2/2 | 48.0 | 46.05 | 0.16 | 0.16 | 4, 4 |
| v2-07-cmake-cross | 1/1 | 1/1 | 1/1 | 1/1 | 72.8 | 65.2 | 0.17 | 0.16 | 0 |
| v2-08-fd-leak | 2/2 | 2/2 | 2/2 | 2/2 | 40.95 | 51.65 | 0.1 | 0.11 | 0, 0 |
| all | 14/14 | 14/14 | 14/14 | 14/14 | 39.95 | 45.95 | 0.11 | 0.14 | 28 |

per trial: cost mean 0.126 USD (max 0.177), total 3.52 USD; agent time mean 47 s (max 166 s); wall cap hits 0; transcript flags 10; guard refusals 0

### pilot-v2-haiku (haiku, 6 scored trials)

| task | A pass | B pass | A root cause | B root cause | A median s | B median s | A median USD | B median USD | B MCP calls (per trial) |
|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | 1/1 | 1/1 | 1/1 | 1/1 | 95.1 | 86.3 | 0.03 | 0.02 | 0 |
| v2-03-resmgr-slog2 | 1/1 | 1/1 | 1/1 | 1/1 | 60.3 | 366.3 | 0.01 | 0.02 | 0 |
| v2-08-fd-leak | 1/1 | 1/1 | 1/1 | 1/1 | 57.8 | 89.0 | 0.01 | 0.02 | 0 |
| all | 3/3 | 3/3 | 3/3 | 3/3 | 60.3 | 89.0 | 0.01 | 0.02 | 0 |

per trial: cost mean 0.017 USD (max 0.025), total 0.10 USD; agent time mean 126 s (max 366 s); wall cap hits 0; transcript flags 1; guard refusals 0

Root cause (blind judge): 28/28 and 6/6; location 28/28 and 6/6; the deterministic `fix_location_hit` 23/28 (Sonnet) and 6/6 (Haiku). No trial hit a cap.

How arm B used qnx-mcp. Sonnet called qnx-mcp tools in 7 of 14 arm B trials, all on the three crash tasks (v2-01, v2-04, v2-06: 4 to 6 calls each, loaded through one ToolSearch call); on the hang, deadlock, slog2, CMake and fd-leak tasks it used Bash and SSH only. Haiku made no ToolSearch call and no MCP call in its 3 arm B trials.

Transcript flags, reviewed by hand (11 in total). Three "host-destructive" flags (e7ab497, edc85e8, e772a50) are `pkill` inside the quoted command that ssh ran on the trial's own target, which the scan cannot tell apart from a command on the build host. Eight "another target stopped or started" flags are other projects' QEMU targets (a qnx-codelab target `cl1` starting or stopping, and two QEMU processes that exited between `pgrep` and `ps`). The ssh guard logged 276 (Sonnet) and 50 (Haiku) calls and refused none.

What this shows: with a root shell over SSH, both models fixed every v2 task that ran in both arms; adding qnx-mcp did not change the pass rate, and Sonnet's arm B was slightly slower and more expensive (median 46 s and 0.14 USD against 40 s and 0.11 USD). The Haiku sample is three pairs, and the Sonnet sample has one or two pairs per task, so no difference between arms can be read from it.
