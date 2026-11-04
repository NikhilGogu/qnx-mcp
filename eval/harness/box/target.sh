#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Trial targets: copies of one golden mkqnximage directory, each with its own
# MAC address, under ~/eval-targets/trials/<name>. Never touches anything else.
#   target.sh clone <name>     copy the golden image, set a MAC derived from the name
#   target.sh start <name>     boot headless, wait for a new DHCP lease and SSH, print the IP
#   target.sh ip <name>        newest lease IP for the target's MAC
#   target.sh stop <name>      mkqnximage --stop, then make sure that QEMU is gone
#   target.sh destroy <name>   stop and delete the copy
set -uo pipefail
ROOT=$HOME/eval-targets
GOLDEN=$ROOT/golden
LEASES=/var/lib/libvirt/dnsmasq/virbr0.status
SSHO=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=3)
cmd=${1:?usage: target.sh clone|start|ip|stop|destroy <name>}
name=${2:?target name}
[[ $name =~ ^e[a-z0-9-]{1,30}$ ]] || { echo "invalid target name: $name" >&2; exit 2; }
D=$ROOT/trials/$name

mac() { printf '52:54:00:%s\n' "$(printf '%s' "$name" | md5sum | sed 's/^\(..\)\(..\)\(..\).*/\1:\2:\3/')"; }

# lease <min_expiry>: newest lease IP for our MAC with expiry-time > min_expiry
lease() {
    python3 -I - "$LEASES" "$(mac)" "${1:-0}" <<'PY'
import json, sys
path, mac, floor = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    leases = json.load(open(path))
except (OSError, ValueError):
    leases = []
mine = sorted((l for l in leases if l.get("mac-address", "").lower() == mac and l.get("expiry-time", 0) > floor),
              key=lambda l: l["expiry-time"])
print(mine[-1]["ip-address"] if mine else "")
PY
}

max_expiry() {
    python3 -I - "$LEASES" "$(mac)" <<'PY'
import json, sys
try:
    leases = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    leases = []
print(max([l.get("expiry-time", 0) for l in leases if l.get("mac-address", "").lower() == sys.argv[2]] or [0]))
PY
}

qemu_pid() {
    local p
    p=$(cat "$D/output/qemu.pid" 2>/dev/null) || return 1
    grep -q qemu-system "/proc/$p/cmdline" 2>/dev/null && [ "$(readlink "/proc/$p/cwd")" = "$D" ] && echo "$p"
}

case $cmd in
clone)
    [ -e "$D" ] && { echo "already exists: $D" >&2; exit 1; }
    mkdir -p "$ROOT/trials" && cp -a "$GOLDEN" "$D" || exit 1
    m=$(mac)
    sed -i "s/^OPT_MACADDR=.*/OPT_MACADDR='$m'/" "$D/local/options" "$D/output/options"
    echo "$D $m"
    ;;
start)
    [ -d "$D" ] || { echo "no such target: $D" >&2; exit 1; }
    floor=$(max_expiry)
    ( cd "$D" && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && mkqnximage --noprompt --run=-h ) > "$D.run.log" 2>&1
    for _ in $(seq 1 120); do
        ip=$(lease "$floor")
        if [ -n "$ip" ] && ssh "${SSHO[@]}" "root@$ip" true 2>/dev/null; then echo "$ip"; exit 0; fi
        sleep 1
    done
    echo "target $name did not come up (log $D.run.log)" >&2
    exit 1
    ;;
ip)
    lease 0
    ;;
stop)
    [ -d "$D" ] || exit 0
    ( cd "$D" && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && mkqnximage --stop ) > /dev/null 2>&1
    for _ in $(seq 1 10); do qemu_pid > /dev/null || exit 0; sleep 1; done
    p=$(qemu_pid) && kill "$p" && sleep 2
    p=$(qemu_pid) && kill -9 "$p"
    exit 0
    ;;
destroy)
    "$0" stop "$name"
    rm -rf "$D" "$D.run.log"
    ;;
*)
    echo "unknown command: $cmd" >&2
    exit 2
    ;;
esac
