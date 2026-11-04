#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# prepare_run.sh <run_dir> <task_dir> <target> <ip> <arm>
# Fresh workspace for one trial: the pinned QNX agent skills workspace, the
# task's visible files, TARGET.md for this target, and (arms B and C) the MCP
# config and the qnx-mcp skill. Arm C has no shell on the target: its TARGET.md
# says so, and only qnx-mcp's own ssh/scp wrappers ($R/bin-mcp) allow the target.
set -euo pipefail
R=$1 TASKDIR=$2 TID=$3 IP=$4 ARM=$5 SERVER=${6:-$HOME/work/qnx-mcp-eval/server-snapshot}
E=$HOME/work/qnx-mcp-eval
[ -e "$R" ] && { echo "run dir exists: $R" >&2; exit 1; }
mkdir -p "$R/claude-config" "$R/mcp-state"
cp -a "$E/runtime/qnx-agent-skills" "$R/ws"
mkdir -p "$R/ws/task"
cp -a "$TASKDIR" "$R/ws/task/"
cp -a "$TASKDIR" "$R/task-orig"
cat > "$R/ssh_config" <<CFG
Host target $IP
    HostName $IP
    User root
    IdentityFile $HOME/.ssh/id_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
    ConnectTimeout 5
CFG
for b in bin bin-mcp; do
    mkdir -p "$R/$b"
    for t in ssh scp sftp; do
        printf '#!/bin/sh\nexec python3 -I %s %s "$@"\n' "$E/harness/box/ssh_guard.py" "$t" > "$R/$b/$t"
        chmod +x "$R/$b/$t"
    done
done
TMPL=TARGET.md.tmpl
[ "$ARM" = C ] && TMPL=TARGET.md.C.tmpl
sed -e "s|{TID}|$TID|g" -e "s|{IP}|$IP|g" -e "s|{TARGET_DIR}|$HOME/eval-targets/trials/$TID|g" -e "s|{SSHCFG}|$R/ssh_config|g" \
    "$E/harness/box/$TMPL" > "$R/ws/TARGET.md"
case $ARM in
    B) cp -a "$E/harness/box/skills/qnx-mcp" "$R/ws/skills/" ;;
    C) cp -a "$E/harness/box/skills-c/qnx-mcp" "$R/ws/skills/" ;;
esac
( cd "$R/ws" && git init -q && git add -A && git -c user.name=workspace -c user.email=workspace@localhost commit -qm workspace )
if [ "$ARM" = C ]; then
    cat > "$R/mcp.json" <<JSON
{"mcpServers": {"qnx": {"type": "stdio", "command": "$SERVER/venv/bin/qnx-mcp", "args": [],
  "env": {"QNX_MCP_SDP_ENV": "$HOME/qnx800/qnxsdp-env.sh", "QNX_MCP_TARGETS_DIR": "$HOME/eval-targets/trials",
          "QNX_MCP_STATE_DIR": "$R/mcp-state", "QNX_MCP_ALLOW_TARGETS": "$TID", "QNX_MCP_WORKSPACE": "$R/ws",
          "QNX_MCP_SSH_KEY": "$HOME/.ssh/id_ed25519", "HOME": "$HOME",
          "PATH": "$R/bin-mcp:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
          "EVAL_GUARD_ALLOW": "$IP", "EVAL_GUARD_LOG": "$R/mcp-ssh.log"}}}}
JSON
elif [ "$ARM" = B ]; then
    cat > "$R/mcp.json" <<JSON
{"mcpServers": {"qnx": {"type": "stdio", "command": "$SERVER/venv/bin/qnx-mcp", "args": [],
  "env": {"QNX_MCP_SDP_ENV": "$HOME/qnx800/qnxsdp-env.sh", "QNX_MCP_TARGETS_DIR": "$HOME/eval-targets/trials",
          "QNX_MCP_STATE_DIR": "$R/mcp-state", "QNX_MCP_ALLOW_TARGETS": "$TID",
          "QNX_MCP_SSH_KEY": "$HOME/.ssh/id_ed25519"}}}}
JSON
fi
echo "$R"
