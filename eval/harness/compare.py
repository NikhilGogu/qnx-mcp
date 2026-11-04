#!/usr/bin/env python3
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
"""Arm A versus arm B per task for one or more result directories (one model each).

usage: compare.py <results dir>...   prints a markdown table per directory
"""
import json
import statistics
import sys
from pathlib import Path


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def main() -> None:
    for d in sys.argv[1:]:
        recs = [json.loads(p.read_text()) for p in sorted(Path(d).glob("*/metrics.json"))]
        recs = [r for r in recs if "harness_error" not in r]
        model = recs[0].get("model", "?") if recs else "?"
        print(f"\n### {Path(d).name} ({model}, {len(recs)} scored trials)\n")
        print("| task | A pass | B pass | A root cause | B root cause | A median s | B median s | A median USD | B median USD | B MCP calls (per trial) |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        tasks = sorted({r["task"] for r in recs})
        for t in tasks + ["all"]:
            row = [t]
            g = {a: [r for r in recs if (t == "all" or r["task"] == t) and r["arm"] == a] for a in "AB"}
            for a in "AB":
                row.append(f"{sum(r['oracle_pass'] for r in g[a])}/{len(g[a])}")
            for a in "AB":
                row.append(f"{sum(1 for r in g[a] if (r.get('judge') or {}).get('root_cause') is True)}/{len(g[a])}")
            for key in ("wall_s", "cost_usd"):
                for a in "AB":
                    row.append(str(med([r[key] for r in g[a]])))
            row.append(", ".join(str(r["mcp_calls"]) for r in g["B"]) if t != "all" else str(sum(r["mcp_calls"] for r in g["B"])))
            print("| " + " | ".join(row) + " |")
        costs = [r["cost_usd"] for r in recs]
        walls = [r["wall_s"] for r in recs if r.get("wall_s")]
        flags = sum(len(r.get("unsafe", [])) for r in recs)
        refused = sum((r.get("guard") or {}).get("refused", 0) for r in recs)
        print(f"\nper trial: cost mean {statistics.mean(costs):.3f} USD (max {max(costs):.3f}), total {sum(costs):.2f} USD; "
              f"agent time mean {statistics.mean(walls):.0f} s (max {max(walls):.0f} s); wall cap hits {sum(1 for r in recs if r.get('wall_capped'))}; "
              f"transcript flags {flags}; guard refusals {refused}")


if __name__ == "__main__":
    main()
