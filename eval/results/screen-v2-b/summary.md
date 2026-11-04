# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 1 | e62a3f4 | pass | True | True | True | 32.8 | 11 | 10 (0/8) | 261670 | 3266 | 0.1172 |  | 0 |
| v2-01-config-overflow | A | 2 | ea95250 | pass | True | True | True | 41.6 | 12 | 11 (0/9) | 315599 | 3707 | 0.1374 |  | 0 |
| v2-02-mp-deadlock | A | 1 | eed2b8d | pass | True | True | False | 34.8 | 10 | 9 (0/7) | 193509 | 3115 | 0.1029 |  | 0 |
| v2-02-mp-deadlock | A | 2 | eb2d3c3 | pass | True | True | False | 33.3 | 10 | 9 (0/8) | 192788 | 3174 | 0.104 |  | 0 |
| v2-03-resmgr-slog2 | A | 1 | efcbc8c | pass | True | True | True | 36.6 | 8 | 7 (0/5) | 146901 | 3458 | 0.1102 |  | 0 |
| v2-03-resmgr-slog2 | A | 2 | eb8b925 | pass | True | True | True | 39.6 | 9 | 8 (0/7) | 209568 | 3558 | 0.1198 |  | 1 |
| v2-04-deep-expr | A | 1 | eb345d9 | pass | True | True | True | 30.4 | 8 | 7 (0/5) | 175159 | 3092 | 0.1068 |  | 1 |
| v2-04-deep-expr | A | 2 | e71cd89 | pass | True | True | True | 46.2 | 10 | 9 (0/7) | 274630 | 3937 | 0.1281 |  | 0 |
| v2-05-hang-states | A | 1 | e9ad6f0 | pass | True | True | True | 39.0 | 7 | 6 (0/5) | 156432 | 2785 | 0.0902 |  | 1 |
| v2-05-hang-states | A | 2 | ef36996 | pass | True | True | True | 43.1 | 8 | 7 (0/4) | 163184 | 3349 | 0.0997 |  | 0 |
| v2-06-worker-null | A | 1 | eb2bf32 | pass | True | True | True | 36.9 | 10 | 9 (0/6) | 232897 | 3394 | 0.1159 |  | 0 |
| v2-06-worker-null | A | 2 | e5f3e0d | pass | True | True | True | 38.1 | 11 | 10 (0/8) | 240721 | 2990 | 0.116 |  | 0 |
| v2-07-cmake-cross | A | 1 | efdc054 | pass | True | True | True | 51.4 | 16 | 15 (0/13) | 405205 | 4180 | 0.1451 |  | 0 |
| v2-07-cmake-cross | A | 2 | ed07fe0 | pass | True | True | True | 57.9 | 15 | 14 (0/13) | 424857 | 5029 | 0.1646 |  | 0 |
| v2-08-fd-leak | A | 1 | e308e21 | pass | True | True | True | 40.2 | 11 | 10 (0/9) | 242902 | 3363 | 0.1056 |  | 0 |
| v2-08-fd-leak | A | 2 | e2848d7 | pass | True | True | True | 49.9 | 12 | 11 (0/8) | 264687 | 3877 | 0.1218 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 2 | 2/2 | 2/2 | 37.2 | 11.5 | 10.5 | 0.13 |
| v2-02-mp-deadlock | A | 2 | 2/2 | 2/2 | 34.05 | 10.0 | 9.0 | 0.1 |
| v2-03-resmgr-slog2 | A | 2 | 2/2 | 2/2 | 38.1 | 8.5 | 7.5 | 0.12 |
| v2-04-deep-expr | A | 2 | 2/2 | 2/2 | 38.3 | 9.0 | 8.0 | 0.12 |
| v2-05-hang-states | A | 2 | 2/2 | 2/2 | 41.05 | 7.5 | 6.5 | 0.09 |
| v2-06-worker-null | A | 2 | 2/2 | 2/2 | 37.5 | 10.5 | 9.5 | 0.12 |
| v2-07-cmake-cross | A | 2 | 2/2 | 2/2 | 54.65 | 15.5 | 14.5 | 0.15 |
| v2-08-fd-leak | A | 2 | 2/2 | 2/2 | 45.05 | 11.5 | 10.5 | 0.11 |
| all | A | 16 | 16/16 | 16/16 | 39.3 | 10.0 | 9.0 | 0.12 |

# Cost and duration

```
{
  "trials": 16,
  "scored": 16,
  "cost_total_usd": 1.89,
  "cost_mean_usd": 0.118,
  "cost_max_usd": 0.1646,
  "wall_mean_s": 40.7,
  "wall_max_s": 57.9,
  "judge_cost_usd": 0.0926
}
```
