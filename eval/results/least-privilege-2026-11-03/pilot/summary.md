# Per trial

| model | task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sonnet | v2-01-config-overflow | C | 1 | ec6f70f | pass | True | True | True | 53.4 | 18 | 17 (8/6) | 516990 | 3901 | 0.1988 |  | 0 |
| sonnet | v2-02-mp-deadlock | C | 1 | ede5046 | pass | True | True | False | 154.4 | 37 | 36 (27/6) | 801219 | 7858 | 0.2744 |  | 0 |
| sonnet | v2-03-resmgr-slog2 | C | 1 | e81a322 | pass | True | True | True | 54.6 | 22 | 21 (13/3) | 478188 | 4779 | 0.1986 |  | 0 |
| sonnet | v2-04-deep-expr | C | 1 | e9b7090 | pass | True | True | False | 51.9 | 16 | 15 (7/5) | 406635 | 3436 | 0.1744 |  | 0 |
| sonnet | v2-05-hang-states | C | 1 | e4e0388 | pass | True | True | False | 49.1 | 18 | 17 (8/5) | 359674 | 3959 | 0.1507 |  | 0 |
| sonnet | v2-06-worker-null | C | 1 | e07021c | pass | True | True | True | 40.5 | 19 | 18 (11/4) | 409894 | 3099 | 0.176 |  | 0 |
| sonnet | v2-07-cmake-cross | C | 1 | e8ea57f | pass | True | True | True | 79.4 | 24 | 23 (5/15) | 754590 | 6426 | 0.2377 |  | 0 |
| sonnet | v2-08-fd-leak | C | 1 | e94ef1b | pass | True | True | True | 64.7 | 27 | 26 (16/6) | 694504 | 5893 | 0.2222 |  | 0 |

# Per task and arm

| model | task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|---|
| sonnet | v2-01-config-overflow | C | 1 | 1/1 | 1/1 | 53.4 | 18 | 17 | 0.2 |
| sonnet | v2-02-mp-deadlock | C | 1 | 1/1 | 1/1 | 154.4 | 37 | 36 | 0.27 |
| sonnet | v2-03-resmgr-slog2 | C | 1 | 1/1 | 1/1 | 54.6 | 22 | 21 | 0.2 |
| sonnet | v2-04-deep-expr | C | 1 | 1/1 | 1/1 | 51.9 | 16 | 15 | 0.17 |
| sonnet | v2-05-hang-states | C | 1 | 1/1 | 1/1 | 49.1 | 18 | 17 | 0.15 |
| sonnet | v2-06-worker-null | C | 1 | 1/1 | 1/1 | 40.5 | 19 | 18 | 0.18 |
| sonnet | v2-07-cmake-cross | C | 1 | 1/1 | 1/1 | 79.4 | 24 | 23 | 0.24 |
| sonnet | v2-08-fd-leak | C | 1 | 1/1 | 1/1 | 64.7 | 27 | 26 | 0.22 |
| sonnet | all | C | 8 | 8/8 | 8/8 | 54.0 | 20.5 | 19.5 | 0.2 |

# Cost and duration

```
{
  "trials": 8,
  "scored": 8,
  "cost_total_usd": 1.63,
  "cost_mean_usd": 0.204,
  "cost_max_usd": 0.2744,
  "wall_mean_s": 68.5,
  "wall_max_s": 154.4,
  "judge_cost_usd": 0.0483
}
```
