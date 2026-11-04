# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 1 | e68c734 | pass | True | True | True | 36.6 | 11 | 10 (0/8) | 268195 | 3494 | 0.1223 |  | 0 |
| v2-01-config-overflow | B | 1 | e6851dc | pass | True | True | True | 47.7 | 12 | 11 (4/4) | 336471 | 3654 | 0.1487 |  | 0 |
| v2-01-config-overflow | A | 2 | ed90cd8 | pass | True | True | True | 39.6 | 11 | 10 (0/8) | 267851 | 3733 | 0.1244 |  | 0 |
| v2-01-config-overflow | B | 2 | e3ef2b0 | pass | True | True | True | 55.7 | 16 | 15 (6/6) | 438406 | 4425 | 0.1774 |  | 1 |
| v2-02-mp-deadlock | A | 1 | eb5accf | pass | True | True | False | 34.4 | 10 | 9 (0/7) | 196011 | 3058 | 0.1008 |  | 0 |
| v2-02-mp-deadlock | B | 1 | e4776e7 | pass | True | True | False | 29.7 | 9 | 8 (0/6) | 169401 | 2736 | 0.0966 |  | 1 |
| v2-02-mp-deadlock | A | 2 | e847789 | pass | True | True | False | 31.3 | 9 | 8 (0/6) | 171522 | 3036 | 0.1021 |  | 1 |
| v2-02-mp-deadlock | B | 2 | edc85e8 | pass | True | True | False | 33.5 | 10 | 9 (0/8) | 209558 | 3152 | 0.1102 |  | 2 |
| v2-03-resmgr-slog2 | A | 2 | e3e5a5e | pass | True | True | True | 35.2 | 8 | 7 (0/6) | 172316 | 3182 | 0.111 |  | 0 |
| v2-03-resmgr-slog2 | B | 2 | e7ab497 | pass | True | True | True | 166.4 | 13 | 12 (0/9) | 308354 | 4203 | 0.1446 |  | 1 |
| v2-04-deep-expr | A | 1 | eeeb060 | pass | True | True | True | 28.0 | 8 | 7 (0/5) | 170571 | 2772 | 0.1042 |  | 0 |
| v2-04-deep-expr | B | 1 | ef78e92 | pass | True | True | True | 37.2 | 14 | 13 (6/4) | 351173 | 3080 | 0.1469 |  | 0 |
| v2-04-deep-expr | A | 2 | ed4622f | pass | True | True | True | 46.2 | 11 | 10 (0/8) | 277980 | 3963 | 0.1291 |  | 0 |
| v2-04-deep-expr | B | 2 | e0b1d53 | pass | True | True | True | 44.7 | 12 | 11 (4/5) | 332066 | 3315 | 0.1398 |  | 0 |
| v2-05-hang-states | A | 1 | e1424c8 | pass | True | True | True | 41.1 | 8 | 7 (0/6) | 157230 | 3146 | 0.0943 |  | 0 |
| v2-05-hang-states | B | 1 | e2ac609 | pass | True | True | True | 24.6 | 7 | 6 (0/4) | 132976 | 2539 | 0.0874 |  | 0 |
| v2-05-hang-states | A | 2 | eb4b89f | pass | True | True | False | 37.6 | 9 | 8 (0/5) | 158156 | 3013 | 0.093 |  | 1 |
| v2-05-hang-states | B | 2 | eb5b3d3 | pass | True | True | True | 34.5 | 6 | 5 (0/4) | 131458 | 2716 | 0.0878 |  | 1 |
| v2-06-worker-null | A | 1 | e9d9572 | pass | True | True | True | 53.8 | 16 | 15 (0/13) | 466791 | 4720 | 0.1705 |  | 0 |
| v2-06-worker-null | B | 1 | ed29b41 | pass | True | True | True | 40.9 | 13 | 12 (4/6) | 385709 | 3316 | 0.1611 |  | 0 |
| v2-06-worker-null | A | 2 | e1e6611 | pass | True | True | True | 42.2 | 10 | 9 (0/8) | 297806 | 3499 | 0.1457 |  | 0 |
| v2-06-worker-null | B | 2 | e07ca4f | pass | True | True | True | 51.2 | 14 | 13 (4/6) | 444891 | 3425 | 0.1642 |  | 1 |
| v2-07-cmake-cross | A | 2 | e29ad4e | pass | True | True | True | 72.8 | 16 | 15 (0/14) | 464633 | 5689 | 0.171 |  | 0 |
| v2-07-cmake-cross | B | 2 | e7cc3bd | pass | True | True | True | 65.2 | 15 | 14 (0/13) | 423398 | 5240 | 0.1627 |  | 0 |
| v2-08-fd-leak | A | 1 | e1fdcda | pass | True | True | True | 40.3 | 8 | 7 (0/6) | 159993 | 2852 | 0.0954 |  | 1 |
| v2-08-fd-leak | B | 1 | e26ba8f | pass | True | True | True | 56.1 | 10 | 9 (0/8) | 262933 | 3262 | 0.1129 |  | 0 |
| v2-08-fd-leak | A | 2 | eb20c58 | pass | True | True | True | 41.6 | 8 | 7 (0/5) | 194401 | 3011 | 0.1012 |  | 0 |
| v2-08-fd-leak | B | 2 | e35602f | pass | True | True | True | 47.2 | 9 | 8 (0/7) | 232656 | 3530 | 0.114 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|
| v2-01-config-overflow | A | 2 | 2/2 | 2/2 | 38.1 | 11.0 | 10.0 | 0.12 |
| v2-01-config-overflow | B | 2 | 2/2 | 2/2 | 51.7 | 14.0 | 13.0 | 0.16 |
| v2-02-mp-deadlock | A | 2 | 2/2 | 2/2 | 32.85 | 9.5 | 8.5 | 0.1 |
| v2-02-mp-deadlock | B | 2 | 2/2 | 2/2 | 31.6 | 9.5 | 8.5 | 0.1 |
| v2-03-resmgr-slog2 | A | 1 | 1/1 | 1/1 | 35.2 | 8 | 7 | 0.11 |
| v2-03-resmgr-slog2 | B | 1 | 1/1 | 1/1 | 166.4 | 13 | 12 | 0.14 |
| v2-04-deep-expr | A | 2 | 2/2 | 2/2 | 37.1 | 9.5 | 8.5 | 0.12 |
| v2-04-deep-expr | B | 2 | 2/2 | 2/2 | 40.95 | 13.0 | 12.0 | 0.14 |
| v2-05-hang-states | A | 2 | 2/2 | 2/2 | 39.35 | 8.5 | 7.5 | 0.09 |
| v2-05-hang-states | B | 2 | 2/2 | 2/2 | 29.55 | 6.5 | 5.5 | 0.09 |
| v2-06-worker-null | A | 2 | 2/2 | 2/2 | 48.0 | 13.0 | 12.0 | 0.16 |
| v2-06-worker-null | B | 2 | 2/2 | 2/2 | 46.05 | 13.5 | 12.5 | 0.16 |
| v2-07-cmake-cross | A | 1 | 1/1 | 1/1 | 72.8 | 16 | 15 | 0.17 |
| v2-07-cmake-cross | B | 1 | 1/1 | 1/1 | 65.2 | 15 | 14 | 0.16 |
| v2-08-fd-leak | A | 2 | 2/2 | 2/2 | 40.95 | 8.0 | 7.0 | 0.1 |
| v2-08-fd-leak | B | 2 | 2/2 | 2/2 | 51.65 | 9.5 | 8.5 | 0.11 |
| all | A | 14 | 14/14 | 14/14 | 39.95 | 9.5 | 8.5 | 0.11 |
| all | B | 14 | 14/14 | 14/14 | 45.95 | 12.0 | 11.0 | 0.14 |

# Cost and duration

```
{
  "trials": 28,
  "scored": 28,
  "cost_total_usd": 3.52,
  "cost_mean_usd": 0.126,
  "cost_max_usd": 0.1774,
  "wall_mean_s": 47.0,
  "wall_max_s": 166.4,
  "judge_cost_usd": 0.1415
}
```
