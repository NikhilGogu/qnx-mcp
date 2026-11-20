# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 1 | e22c4ee | pass | True | True | True | 9 | 8 (0/7) | 226186 | 2700 | 0.1064 |  | 0 |
| v2-01-config-overflow | A | 2 | e3ce106 | pass | True | True | True | 11 | 10 (0/7) | 266899 | 2735 | 0.111 |  | 0 |
| v2-02-mp-deadlock | A | 1 | e10e80b | pass | True | True | True | 10 | 9 (0/7) | 199803 | 2985 | 0.1022 |  | 0 |
| v2-02-mp-deadlock | A | 2 | ec3c55e | pass | True | True | True | 12 | 11 (0/9) | 226688 | 2951 | 0.1057 |  | 0 |
| v2-03-resmgr-slog2 | A | 1 | e74910b | pass | True | True | True | 1 | 9 (0/8) | 37770 | 247 | 0.2193 |  | 1 |
| v2-03-resmgr-slog2 | A | 2 | ecde8c2 | pass | True | True | True | 10 | 9 (0/7) | 253809 | 3245 | 0.1201 |  | 0 |
| v2-04-deep-expr | A | 1 | eb57843 | pass | True | True | True | 10 | 9 (0/7) | 233249 | 3618 | 0.1165 |  | 0 |
| v2-04-deep-expr | A | 2 | eb7c95a | pass | True | True | True | 6 | 5 (0/5) | 158760 | 2591 | 0.0933 |  | 1 |
| v2-05-hang-states | A | 1 | e587287 | pass | True | True | True | 8 | 7 (0/5) | 156464 | 2787 | 0.0889 |  | 0 |
| v2-05-hang-states | A | 2 | ea56923 | pass | True | True | True | 10 | 9 (0/7) | 213608 | 3036 | 0.0978 |  | 1 |
| v2-06-worker-null | A | 1 | e3f9b82 | pass | True | True | True | 10 | 9 (0/8) | 273973 | 3037 | 0.1252 |  | 0 |
| v2-06-worker-null | A | 2 | e4024dc | pass | True | True | True | 8 | 7 (0/5) | 166746 | 2595 | 0.1017 |  | 0 |
| v2-07-cmake-cross | A | 1 | e6501a2 | pass | True | True | True | 12 | 11 (0/10) | 287932 | 3534 | 0.1203 |  | 0 |
| v2-07-cmake-cross | A | 2 | e12acbc | pass | True | True | True | 11 | 10 (0/9) | 265805 | 3582 | 0.1213 |  | 0 |
| v2-08-fd-leak | A | 1 | ee52e61 | pass | True | True | True | 14 | 13 (0/12) | 356377 | 4772 | 0.1406 |  | 1 |
| v2-08-fd-leak | A | 2 | e987b31 | pass | True | True | True | 7 | 6 (0/5) | 160929 | 3209 | 0.0974 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 2 | 2/2 | 2/2 | 10.0 | 9.0 | 0.11 |
| v2-02-mp-deadlock | A | 2 | 2/2 | 2/2 | 11.0 | 10.0 | 0.1 |
| v2-03-resmgr-slog2 | A | 2 | 2/2 | 2/2 | 5.5 | 9.0 | 0.17 |
| v2-04-deep-expr | A | 2 | 2/2 | 2/2 | 8.0 | 7.0 | 0.1 |
| v2-05-hang-states | A | 2 | 2/2 | 2/2 | 9.0 | 8.0 | 0.09 |
| v2-06-worker-null | A | 2 | 2/2 | 2/2 | 9.0 | 8.0 | 0.11 |
| v2-07-cmake-cross | A | 2 | 2/2 | 2/2 | 11.5 | 10.5 | 0.12 |
| v2-08-fd-leak | A | 2 | 2/2 | 2/2 | 10.5 | 9.5 | 0.12 |
| all | A | 16 | 16/16 | 16/16 | 10.0 | 9.0 | 0.11 |

# Cost and duration

```
{
  "trials": 16,
  "scored": 16,
  "cost_total_usd": 1.87,
  "cost_mean_usd": 0.117,
  "cost_max_usd": 0.2193,
  "judge_cost_usd": 0.0864
}
```
