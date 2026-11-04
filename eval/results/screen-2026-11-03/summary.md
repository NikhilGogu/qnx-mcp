# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t02-thread-stack | A | 1 | e10d841 | pass | True | True | True | 28.5 | 6 | 5 (0/4) | 118249 | 2322 | 0.0733 |  | 0 |
| t03-socket-link | A | 1 | eb0d1be | pass | True | True | True | 24.1 | 9 | 8 (0/5) | 151768 | 1966 | 0.0774 |  | 0 |
| t04-deadlock | A | 1 | efb819a | pass | True | True | True | 50.3 | 8 | 7 (0/4) | 143628 | 2191 | 0.0741 |  | 0 |
| t06-resmgr-errno | A | 1 | e2eebc6 | pass | True | True | True | 123.2 | 11 | 10 (0/6) | 219326 | 3234 | 0.1034 |  | 0 |
| t07-double-free | A | 1 | e8178cc | pass | True | True | True | 21.6 | 7 | 6 (0/4) | 143622 | 2020 | 0.0719 |  | 0 |
| t08-no-timeout | A | 1 | e550882 | pass | True | True | True | 30.5 | 7 | 6 (0/4) | 122461 | 2859 | 0.0813 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|
| t02-thread-stack | A | 1 | 1/1 | 1/1 | 28.5 | 6 | 5 | 0.07 |
| t03-socket-link | A | 1 | 1/1 | 1/1 | 24.1 | 9 | 8 | 0.08 |
| t04-deadlock | A | 1 | 1/1 | 1/1 | 50.3 | 8 | 7 | 0.07 |
| t06-resmgr-errno | A | 1 | 1/1 | 1/1 | 123.2 | 11 | 10 | 0.1 |
| t07-double-free | A | 1 | 1/1 | 1/1 | 21.6 | 7 | 6 | 0.07 |
| t08-no-timeout | A | 1 | 1/1 | 1/1 | 30.5 | 7 | 6 | 0.08 |
| all | A | 6 | 6/6 | 6/6 | 29.5 | 7.5 | 6.5 | 0.08 |

# Cost and duration

```
{
  "trials": 6,
  "scored": 6,
  "cost_total_usd": 0.48,
  "cost_mean_usd": 0.08,
  "cost_max_usd": 0.1034,
  "wall_mean_s": 46.4,
  "wall_max_s": 123.2,
  "judge_cost_usd": 0.0277
}
```
