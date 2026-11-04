#!/usr/bin/env python3
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
"""For each trial: tool-call index of the first source read, first target interaction and first source edit.

usage: order_check.py <results dir>...
"""
import json
import re
import sys
from pathlib import Path

EDIT_BASH = re.compile(r"(sed -i|perl -pi|python3? -|cat >|tee |patch |git apply|apply_patch)")


def kinds(transcript: Path) -> list[str]:
    out = []
    for line in transcript.read_text().splitlines():
        e = json.loads(line) if line.strip() else {}
        if e.get("type") != "assistant":
            continue
        for b in e["message"].get("content", []):
            if b.get("type") != "tool_use":
                continue
            inp = b["input"]
            c = inp.get("command") or inp.get("file_path") or json.dumps(inp)
            if b["name"] in ("Edit", "Write") and "/task/" in c:
                out.append("edit")
            elif b["name"].startswith("mcp__qnx__deploy") or b["name"].startswith("mcp__qnx__processes") or re.search(r"\b(ssh|scp)\b", c):
                out.append("target")
            elif b["name"] == "Bash" and EDIT_BASH.search(c) and re.search(r"\.(c|h)\b|Makefile", c):
                out.append("edit")
            elif re.search(r"\.c\b", c) and b["name"] in ("Bash", "Read"):
                out.append("readsrc")
            else:
                out.append("other")
    return out


def main() -> None:
    n = before = edit_first = 0
    for d in sys.argv[1:]:
        for m in sorted(Path(d).glob("*/metrics.json")):
            rec = json.loads(m.read_text())
            k = kinds(m.parent / "transcript.jsonl")
            idx = {x: (k.index(x) if x in k else None) for x in ("readsrc", "target", "edit")}
            src_first = idx["readsrc"] is not None and (idx["target"] is None or idx["readsrc"] < idx["target"])
            fix_first = idx["edit"] is not None and idx["target"] is not None and idx["edit"] < idx["target"]
            n += 1
            before += src_first
            edit_first += fix_first
            print(f"{rec['task']:18} {rec['arm']} {rec['target']}  first read={idx['readsrc']} first target={idx['target']} first edit={idx['edit']}"
                  f"  source read before target: {src_first}  edited before any target run: {fix_first}")
    print(f"trials={n} source read before first target interaction={before} edited before any target run={edit_first}")


if __name__ == "__main__":
    main()
