#!/bin/sh
# Every test of qnx-mcp, including the ones that need the QNX SDP and a running target, in one log.
# Run it on the Linux build host from the repository root, with the venv that has qnx-mcp installed:
#
#   QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*' QNX_MCP_E2E_TARGET=tb QNX_MCP_E2E_CREATE=tb2 \
#     scripts/live-tests.sh
#
# QNX_MCP_ALLOW_TARGETS  must allow both targets below (the tests also check that other names are refused)
# QNX_MCP_E2E_TARGET  a running target the tests may use (created with target_create)
# QNX_MCP_E2E_CREATE  a target name the lifecycle test creates, boots and destroys (must not exist)
#
# The log goes to docs/evidence/test-run-<version>.log. Each suite runs even if an earlier one failed,
# and the script exits non-zero if any did.
set -u
: "${QNX_MCP_E2E_TARGET:?set QNX_MCP_E2E_TARGET to a running target}"
: "${QNX_MCP_E2E_CREATE:?set QNX_MCP_E2E_CREATE to a target name that does not exist yet}"
: "${QNX_MCP_ALLOW_TARGETS:?set QNX_MCP_ALLOW_TARGETS, for example 'tb,tb[0-9]*'}"
export QNX_MCP_ALLOW_TARGETS
PYTHON=${PYTHON:-python}

root=$(cd "$(dirname "$0")/.." && pwd)
version=$("$PYTHON" -c 'import qnx_mcp; print(qnx_mcp.__version__)')
log="$root/docs/evidence/test-run-$version.log"
failed=0

suite() {
    echo "== $*" | tee -a "$log"
    (cd "$root/tests" && "$@" 2>&1) | tee -a "$log"
    # tee hides the exit status of the subshell, so the summary line decides
    tail -n 1 "$log" | grep -q -E 'failed|error' && failed=1
}

: > "$log"
{
    echo "qnx-mcp $version, $(git -C "$root" rev-parse --short HEAD), $(date -u +%Y-%m-%dT%H:%MZ)"
    uname -srm
    "$PYTHON" --version
} | tee -a "$log"
suite "$PYTHON" -m pytest -q -p no:asyncio test_units.py test_mcp_offline.py test_skill.py
suite "$PYTHON" -m pytest -q -s -p no:asyncio test_build_sdk.py
suite "$PYTHON" -m pytest -q -s -p no:asyncio test_slice_e2e.py test_shellfree_e2e.py
suite "$PYTHON" -m pytest -q -s -p no:asyncio test_lifecycle_e2e.py
echo "log: $log"
exit $failed
