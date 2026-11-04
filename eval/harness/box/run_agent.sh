#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# run_agent.sh <run_dir>: one headless Claude Code session on Bedrock with the
# caps from run.env. Every arm runs exactly this; mcp.json and the guard's allow list differ.
set -u
R=$1
. "$R/run.env"
cd "$R/ws" || exit 1
# arm C has no shell on the target: the guard refuses every host (qnx-mcp gets its own guard in mcp.json)
GUARD_ALLOW=$TARGET_IP
[ "${ARM:-}" = C ] && GUARD_ALLOW=
export PATH="$R/bin:$PATH" EVAL_GUARD_ALLOW="$GUARD_ALLOW" EVAL_GUARD_LOG="$R/ssh-guard.log"
export CLAUDE_CONFIG_DIR="$R/claude-config" CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=eu-west-1 \
    ANTHROPIC_MODEL="$MODEL" ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL" ANTHROPIC_DEFAULT_HAIKU_MODEL="$SMALL_MODEL" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 \
    MCP_TOOL_TIMEOUT=600000 MCP_TIMEOUT=60000
args=(-p --output-format stream-json --verbose --model "$MODEL" --effort "$EFFORT" --max-turns "$MAX_TURNS"
      --max-budget-usd "$BUDGET_USD" --permission-mode bypassPermissions --strict-mcp-config
      --disallowedTools WebSearch WebFetch)
[ -f "$R/mcp.json" ] && args+=(--mcp-config "$R/mcp.json")
date +%s.%N > "$R/agent.start"
timeout --signal=TERM --kill-after=30 "$WALL_S" "$CLAUDE" "${args[@]}" < "$R/prompt.txt" > "$R/transcript.jsonl" 2> "$R/agent.stderr"
rc=$?
date +%s.%N > "$R/agent.end"
echo "$rc" > "$R/agent.rc"
