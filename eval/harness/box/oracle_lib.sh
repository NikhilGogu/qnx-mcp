# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Helpers sourced by the task oracles. Runs on the build box from a scoring
# directory holding src/ (the agent's final task directory), hidden/ (the
# task's hidden files) and this file. $IP is a freshly booted target.
set -u
SSHO=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=5)
FAILS=0
CHECKS=0

tssh() { local t=$1; shift; timeout -k 3 "$t" ssh "${SSHO[@]}" "root@$IP" "$@"; }

check() {
    CHECKS=$((CHECKS + 1))
    if [ "$2" = 0 ]; then echo "ok   $1"; else echo "FAIL $1${3:+: $3}"; FAILS=$((FAILS + 1)); fi
}

finish() {
    echo "checks=$CHECKS failed=$FAILS"
    if [ "$FAILS" = 0 ] && [ "$CHECKS" -gt 0 ]; then echo "ORACLE PASS"; exit 0; fi
    echo "ORACLE FAIL"; exit 1
}

# build <dir> <binary>...: clean rebuild with the task's own Makefile.
build() {
    local d=$1 rc; shift
    ( cd "$d" && rm -f "$@" ./*.o && source "$HOME/qnx800/qnxsdp-env.sh" >/dev/null 2>&1 && make ) > build.log 2>&1
    rc=$?
    for b in "$@"; do [ -x "$d/$b" ] || rc=1; done
    check "build" "$rc" "$(tail -4 build.log | tr '\n' ' ')"
    return "$rc"
}

# stage <file>...: copy files to /tmp/oracle on the target.
stage() {
    tssh 20 'rm -rf /tmp/oracle && mkdir -p /tmp/oracle' && scp -q "${SSHO[@]}" "$@" "root@$IP:/tmp/oracle/"
    check "stage files on target" $?
}

# expect_out <name> <timeout_s> <exit_code> <expected_stdout_file|-> <remote command>
# Exit code 124 from timeout means the command hung.
expect_out() {
    local name=$1 t=$2 erc=$3 exp=$4 out rc; shift 4
    out=$(tssh "$t" "$@" 2>/dev/null)
    rc=$?
    LAST_OUT=$out
    if [ "$rc" = 124 ] || [ "$rc" = 137 ]; then check "$name" 1 "no result within ${t}s"; return 1; fi
    if [ "$rc" != "$erc" ]; then check "$name" 1 "exit $rc, expected $erc; stdout: $(printf '%s' "$out" | head -4 | tr '\n' '|')"; return 1; fi
    if [ "$exp" != - ] && [ -n "${NUMTOL:-}" ]; then
        if ! numeq <(printf '%s\n' "$out") "$exp"; then
            check "$name" 1 "stdout differs beyond $NUMTOL: $(diff <(printf '%s\n' "$out") "$exp" | head -6 | tr '\n' '|')"; return 1
        fi
    elif [ "$exp" != - ] && ! diff <(printf '%s\n' "$out") "$exp" > /dev/null; then
        check "$name" 1 "stdout differs: $(diff <(printf '%s\n' "$out") "$exp" | head -6 | tr '\n' '|')"; return 1
    fi
    check "$name" 0
}

# numeq <a> <b>: same words, numbers may differ by $NUMTOL (printf rounds exact
# ties differently in QNX libc and glibc, so expected outputs made on Linux can
# differ in the last digit).
numeq() {
    python3 -I - "$1" "$2" "${NUMTOL:-0}" <<'NUMPY'
import re
import sys
a, b, tol = open(sys.argv[1]).read().split(), open(sys.argv[2]).read().split(), float(sys.argv[3])
num = re.compile(r"^[-+]?[0-9]+([.][0-9]+)?$")
ok = len(a) == len(b) and all(x == y or (num.match(x) and num.match(y) and abs(float(x) - float(y)) <= tol) for x, y in zip(a, b))
sys.exit(0 if ok else 1)
NUMPY
}
