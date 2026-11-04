# Per trial

| task | arm | rep | target | oracle | root cause | location | fix in GT region | wall s | turns | tool calls (MCP/Bash) | input tok (incl. cache) | output tok | cost USD | capped | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t01-calib-null | A | 1 | e791d82 | pass | True | True | True | 21.1 | 8 | 7 (0/4) | 145382 | 1926 | 0.0712 |  | 0 |
| t01-calib-null | B | 1 | ef9a50d | pass | True | True | True | 36.9 | 15 | 14 (7/3) | 357845 | 2847 | 0.1231 |  | 0 |
| t01-calib-null | A | 2 | e1dcda9 | pass | True | True | True | 19.8 | 6 | 5 (0/4) | 116505 | 1881 | 0.0667 |  | 0 |
| t01-calib-null | B | 2 | ea3c6d1 | pass | True | True | True | 35.4 | 14 | 13 (6/3) | 298504 | 2818 | 0.1155 |  | 0 |
| t05-msgreply | A | 1 | e549d5d | pass | True | True | True | 25.4 | 7 | 6 (0/4) | 148420 | 2238 | 0.0778 |  | 0 |
| t05-msgreply | B | 1 | eede29d | pass | True | True | True | 151.4 | 9 | 8 (0/5) | 182736 | 2586 | 0.0889 |  | 0 |
| t05-msgreply | A | 2 | ec4f464 | pass | True | True | True | 22.1 | 7 | 6 (0/4) | 121457 | 1901 | 0.0709 |  | 0 |
| t05-msgreply | B | 2 | e248698 | pass | True | True | True | 30.6 | 8 | 7 (0/4) | 127104 | 2069 | 0.0763 |  | 0 |

# Per task and arm

| task | arm | n | oracle pass | root cause | median wall s | median turns | median tool calls | median cost USD |
|---|---|---|---|---|---|---|---|---|
| t01-calib-null | A | 2 | 2/2 | 2/2 | 20.45 | 7.0 | 6.0 | 0.07 |
| t01-calib-null | B | 2 | 2/2 | 2/2 | 36.15 | 14.5 | 13.5 | 0.12 |
| t05-msgreply | A | 2 | 2/2 | 2/2 | 23.75 | 7.0 | 6.0 | 0.07 |
| t05-msgreply | B | 2 | 2/2 | 2/2 | 91.0 | 8.5 | 7.5 | 0.08 |
| all | A | 4 | 4/4 | 4/4 | 21.6 | 7.0 | 6.0 | 0.07 |
| all | B | 4 | 4/4 | 4/4 | 36.15 | 11.5 | 10.5 | 0.1 |

# Cost and duration

```
{
  "trials": 8,
  "scored": 8,
  "cost_total_usd": 0.69,
  "cost_mean_usd": 0.086,
  "cost_max_usd": 0.1231,
  "wall_mean_s": 42.8,
  "wall_max_s": 151.4,
  "judge_cost_usd": 0.0325
}
```
