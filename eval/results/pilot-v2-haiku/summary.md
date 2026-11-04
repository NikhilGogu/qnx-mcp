# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 1 | e211726 | pass | True | True | True | 95.1 | 14 | 13 (0/12) | 702914 | 17869 | 0.0254 |  | 0 |
| v2-01-config-overflow | B | 1 | e5d9192 | pass | True | True | True | 86.3 | 18 | 17 (0/12) | 778251 | 14530 | 0.0214 |  | 0 |
| v2-03-resmgr-slog2 | A | 1 | e772a50 | pass | True | True | True | 60.3 | 16 | 15 (0/13) | 319942 | 10316 | 0.0124 |  | 1 |
| v2-03-resmgr-slog2 | B | 1 | effc7c3 | pass | True | True | True | 366.3 | 13 | 12 (0/10) | 424946 | 12724 | 0.0151 |  | 0 |
| v2-08-fd-leak | A | 2 | eb005d1 | pass | True | True | True | 57.8 | 11 | 10 (0/9) | 371028 | 7996 | 0.0112 |  | 0 |
| v2-08-fd-leak | B | 2 | e6edbb3 | pass | True | True | True | 89.0 | 14 | 13 (0/12) | 532433 | 14126 | 0.0166 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 1 | 1/1 | 1/1 | 95.1 | 14 | 13 | 0.03 |
| v2-01-config-overflow | B | 1 | 1/1 | 1/1 | 86.3 | 18 | 17 | 0.02 |
| v2-03-resmgr-slog2 | A | 1 | 1/1 | 1/1 | 60.3 | 16 | 15 | 0.01 |
| v2-03-resmgr-slog2 | B | 1 | 1/1 | 1/1 | 366.3 | 13 | 12 | 0.02 |
| v2-08-fd-leak | A | 1 | 1/1 | 1/1 | 57.8 | 11 | 10 | 0.01 |
| v2-08-fd-leak | B | 1 | 1/1 | 1/1 | 89.0 | 14 | 13 | 0.02 |
| all | A | 3 | 3/3 | 3/3 | 60.3 | 14 | 13 | 0.01 |
| all | B | 3 | 3/3 | 3/3 | 89.0 | 14 | 13 | 0.02 |

# Cost and duration

```
{
  "trials": 6,
  "scored": 6,
  "cost_total_usd": 0.1,
  "cost_mean_usd": 0.017,
  "cost_max_usd": 0.0254,
  "wall_mean_s": 125.8,
  "wall_max_s": 366.3,
  "judge_cost_usd": 0.0257
}
```
