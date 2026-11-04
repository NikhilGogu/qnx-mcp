#!/usr/bin/env python3
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
"""Tables for the least-privilege study (phase 3: arm A against arm C, per model).

usage: lp_report.py <results dir> [--exclude TASK ...] [--breach TARGET ...]   prints markdown

--exclude drops tasks from the "without" rows (tasks with a recorded capability gap);
--breach marks arm C trials that reached the target outside qnx-mcp, counted as failures.
"""
import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def load(d: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(d.glob("*/metrics.json"))]


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def interval(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Clopper-Pearson) interval by bisection."""
    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    low = 0.0 if k == 0 else solve(lambda p: 1 - binom_cdf(k - 1, n, p) < alpha / 2)
    high = 1.0 if k == n else solve(lambda p: binom_cdf(k, n, p) > alpha / 2)
    return low, high


def mr(xs, fmt="{:.0f}") -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return ""
    return f"{fmt.format(statistics.median(xs))} [{fmt.format(min(xs))}-{fmt.format(max(xs))}]"


def tokens(r: dict) -> int:
    t = r["tokens"]
    return t["input"] + t["cache_write"] + t["cache_read"] + t["output"]


def passed(r: dict, breach: set) -> bool:
    return bool(r["oracle_pass"]) and r["target"] not in breach


def rc(r: dict) -> bool:
    return (r.get("judge") or {}).get("root_cause") is True


def cell(g: list[dict], breach: set) -> list[str]:
    n = len(g)
    return [str(n), f"{sum(passed(r, breach) for r in g)}/{n}", f"{sum(rc(r) for r in g)}/{n}", mr([r["wall_s"] for r in g]),
            mr([r["num_turns"] for r in g]), mr([tokens(r) / 1000 for r in g]), mr([r["cost_usd"] for r in g], "{:.3f}")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--breach", nargs="*", default=[])
    a = ap.parse_args()
    allrecs = load(Path(a.dir))
    errors = [r for r in allrecs if "harness_error" in r]
    recs = [r for r in allrecs if "harness_error" not in r]
    breach = set(a.breach)
    models = sorted({r["model"] for r in recs}, reverse=True)
    arms = sorted({r["arm"] for r in recs})
    tasks = sorted({r["task"] for r in recs})
    print(f"{len(recs)} scored trials, {len(errors)} harness errors" + (f" ({', '.join(r['target'] for r in errors)})" if errors else ""))
    for mo in models:
        R = [r for r in recs if r["model"] == mo]
        print(f"\n### {mo}: per task and arm\n")
        print("Medians with [minimum-maximum]. Tokens are input (uncached, cache write, cache read) plus output, in thousands.\n")
        print("| task | arm | n | oracle pass | root cause | wall s | turns | tokens (k) | cost USD |")
        print("|---|---|---|---|---|---|---|---|---|")
        for t in tasks:
            for arm in arms:
                g = [r for r in R if r["task"] == t and r["arm"] == arm]
                if g:
                    print("| " + " | ".join([t, arm] + cell(g, breach)) + " |")
        for label, keep in [("all tasks", lambda r: True)] + ([(f"without {', '.join(a.exclude)}", lambda r: r["task"] not in a.exclude)] if a.exclude else []):
            for arm in arms:
                g = [r for r in R if r["arm"] == arm and keep(r)]
                print("| " + " | ".join([f"**{label}**", arm] + cell(g, breach)) + " |")
        print(f"\n### {mo}: totals with exact 95% intervals\n")
        print("| tasks | arm | oracle pass | 95% interval | root cause | total cost USD | wall cap hits |")
        print("|---|---|---|---|---|---|---|")
        sets = [("all", lambda r: True)]
        if a.exclude:
            sets.append((f"without {', '.join(a.exclude)}", lambda r: r["task"] not in a.exclude))
        sets.append(("arm C without trials that ran a shell through remote_binary", lambda r: r["arm"] != "C" or not r.get("remote_binary_shell")))
        for label, keep in sets:
            for arm in arms:
                g = [r for r in R if r["arm"] == arm and keep(r)]
                if not g or (label.startswith("arm C") and arm != "C"):
                    continue
                k = sum(passed(r, breach) for r in g)
                lo, hi = interval(k, len(g))
                print(f"| {label} | {arm} | {k}/{len(g)} | {lo:.2f}-{hi:.2f} | {sum(rc(r) for r in g)}/{len(g)} | "
                      f"{sum(r['cost_usd'] for r in g):.2f} | {sum(1 for r in g if r.get('wall_capped'))} |")
        if set(arms) >= {"A", "C"}:
            pairs = {}
            for r in R:
                pairs.setdefault((r["task"], r["rep"]), {})[r["arm"]] = r
            full = [p for p in pairs.values() if "A" in p and "C" in p]
            both = sum(1 for p in full if passed(p["A"], breach) and passed(p["C"], breach))
            aonly = [f"{p['A']['task']} rep {p['A']['rep']}" for p in full if passed(p["A"], breach) and not passed(p["C"], breach)]
            conly = [f"{p['C']['task']} rep {p['C']['rep']}" for p in full if passed(p["C"], breach) and not passed(p["A"], breach)]
            neither = sum(1 for p in full if not passed(p["A"], breach) and not passed(p["C"], breach))
            print(f"\n### {mo}: paired (C minus A)\n")
            print(f"{len(full)} complete pairs: both pass {both}, only A passes {len(aonly)}" + (f" ({'; '.join(aonly)})" if aonly else "") +
                  f", only C passes {len(conly)}" + (f" ({'; '.join(conly)})" if conly else "") + f", neither {neither}.")
            dw = [p["C"]["wall_s"] - p["A"]["wall_s"] for p in full if p["C"]["wall_s"] and p["A"]["wall_s"]]
            dc = [p["C"]["cost_usd"] - p["A"]["cost_usd"] for p in full]
            dt = [p["C"]["num_turns"] - p["A"]["num_turns"] for p in full]
            print(f"Median difference C minus A [range]: wall {mr(dw)} s, turns {mr(dt)}, cost {mr(dc, '{:+.3f}')} USD.")
        C = [r for r in R if r["arm"] == "C"]
        if C:
            print(f"\n### {mo}: how arm C worked\n")
            tools = Counter()
            for r in C:
                tools.update({k.split("__")[-1]: v for k, v in r["tool_calls_by_name"].items() if k.startswith("mcp__")})
            errs = Counter()
            for r in C:
                errs.update(r.get("mcp_errors") or {})
            progs = Counter(str(x["path"]) for r in C for x in r.get("remote_binary") or [])
            print(f"* MCP calls per trial: {mr([r['mcp_calls'] for r in C])}; trials with no MCP call: {sum(1 for r in C if not r['mcp_calls'])}; "
                  f"ToolSearch calls per trial: {mr([r['tool_calls_by_name'].get('ToolSearch', 0) for r in C])}.")
            print(f"* MCP calls by tool (all {len(C)} trials): " + ", ".join(f"{k} {v}" for k, v in tools.most_common()) + ".")
            print(f"* Agent ssh/scp/sftp calls refused by the guard: {sum(r['guard']['refused'] for r in C)} in "
                  f"{sum(1 for r in C if r['guard']['refused'])} trials; qnx-mcp ssh/scp calls {sum((r.get('mcp_ssh') or {}).get('calls', 0) for r in C)}, "
                  f"hosts other than the trial target: {sum(1 for r in C for h in (r.get('mcp_ssh') or {}).get('hosts', []) if h != r['ip'])}.")
            print(f"* qnx-mcp errors returned to the agent: " + (", ".join(f"{k} {v}" for k, v in errs.most_common()) or "none") + ".")
            print(f"* Programs run with remote_binary: " + (", ".join(f"{k} ({v})" for k, v in progs.most_common()) or "none") +
                  f"; trials that ran a shell this way: {sum(1 for r in C if r.get('remote_binary_shell'))}, "
                  f"a system utility: {sum(1 for r in C if r.get('remote_binary_system'))}.")
        flags = Counter(f["kind"] for r in R for f in r.get("unsafe", []))
        print(f"\n### {mo}: transcript and guard flags (all arms, for review)\n")
        for arm in arms:
            fa = Counter(f["kind"] for r in R if r["arm"] == arm for f in r.get("unsafe", []))
            print(f"* arm {arm}: " + (", ".join(f"{k} ({v})" for k, v in sorted(fa.items())) or "none"))
        del flags
    jc = sum((r.get("judge") or {}).get("cost_usd") or 0 for r in recs)
    print(f"\nAgent cost {sum(r['cost_usd'] for r in recs):.2f} USD (Claude Code list-price estimate), judge {jc:.2f} USD.")


if __name__ == "__main__":
    main()
