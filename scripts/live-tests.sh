#!/bin/sh
# Every test of qnx-mcp, including the ones that need the QNX SDP and a running target, in one log.
# Run it on the Linux build host from the repository root, with the venv that has qnx-mcp installed:
#
#   QNX_MCP_ALLOW_TARGETS='tb,tb[0-9]*' QNX_MCP_E2E_TARGET=tb QNX_MCP_E2E_CREATE=tb2 \
#     QNX_MCP_E2E_RUN_AS=1000:1000 scripts/live-tests.sh
#
# Two kinds of live target, each suite group runs when its variables are set (at least one group is required):
#
# QEMU target (a Linux host with KVM and mkqnximage):
#   QNX_MCP_E2E_TARGET  a running target the tests may use (created with target_create)
#   QNX_MCP_E2E_CREATE  a target name the lifecycle test creates, boots and destroys (must not exist)
#   QNX_MCP_E2E_RUN_AS  numeric uid:gid for the non-root tests; it need not exist in the image's /etc/passwd
#   All three are required together.
#
# SSH-only machine (an existing QNX 8.0 machine, for example QNX's QNX OS 8.0 AMI on AWS Graviton):
#   QNX_MCP_E2E_SSH_HOST         its address; the suite registers it as a target, tests it and unregisters it
#   QNX_MCP_E2E_SSH_FINGERPRINT  SHA256:... of its host key, or set QNX_MCP_TRUST_FIRST_USE=1 (pins the first key; weaker)
#   QNX_MCP_SSH_KEY              private key whose public half the machine accepts (default ~/.ssh/id_ed25519)
#   QNX_MCP_E2E_SSH_ARCH (aarch64le), QNX_MCP_E2E_SSH_USER (qnxuser), QNX_MCP_E2E_SSH_PORT (22),
#   QNX_MCP_E2E_SSH_NAME (ami1; QNX_MCP_ALLOW_TARGETS must allow it)
#   QNX_MCP_RUN_AS must be unset: the login is not root, so the runner cannot drop privileges there.
#   Example:
#     QNX_MCP_ALLOW_TARGETS='ami*' QNX_MCP_E2E_SSH_HOST=ec2-203-0-113-9.eu-west-1.compute.amazonaws.com \
#       QNX_MCP_E2E_SSH_FINGERPRINT=SHA256:... QNX_MCP_SSH_KEY=~/.ssh/qnx-ami scripts/live-tests.sh
#
# QNX_MCP_ALLOW_TARGETS  must allow every target named above (the QEMU tests also check that other names are refused)
#
# The log goes to docs/evidence/test-run-<version>.log. Each suite runs even if an earlier one failed,
# and the script exits non-zero if any did. A suite without a single passed test counts as failed:
# the live tests skip themselves when the SDP or the target is missing, and that is not a live run.
set -u

# pytest's summary line: success needs at least one passed test and no failure or error.
summary_ok() {
    case "$1" in
        *failed*|*error*) return 1 ;;
        [0-9]*" passed"*) return 0 ;;
        *) return 1 ;;
    esac
}
if [ "${1-}" = --summary-ok ]; then
    summary_ok "${2-}"
    exit $?
fi

qemu=0 ssh=0
if [ -n "${QNX_MCP_E2E_TARGET-}${QNX_MCP_E2E_CREATE-}${QNX_MCP_E2E_RUN_AS-}" ]; then
    qemu=1
    : "${QNX_MCP_E2E_TARGET:?set QNX_MCP_E2E_TARGET to a running target}"
    : "${QNX_MCP_E2E_CREATE:?set QNX_MCP_E2E_CREATE to a target name that does not exist yet}"
    : "${QNX_MCP_E2E_RUN_AS:?set QNX_MCP_E2E_RUN_AS to a non-root uid:gid}"
fi
if [ -n "${QNX_MCP_E2E_SSH_HOST-}" ]; then
    ssh=1
    if [ -z "${QNX_MCP_E2E_SSH_FINGERPRINT-}" ] && [ "${QNX_MCP_TRUST_FIRST_USE-}" != 1 ]; then
        echo "set QNX_MCP_E2E_SSH_FINGERPRINT (SHA256:...) or QNX_MCP_TRUST_FIRST_USE=1 for the SSH-only machine" >&2
        exit 2
    fi
    if [ -n "${QNX_MCP_RUN_AS-}" ]; then
        echo "unset QNX_MCP_RUN_AS: an SSH-only machine's login is not root, so programs cannot be run as another user" >&2
        exit 2
    fi
fi
if [ "$qemu$ssh" = 00 ]; then
    echo "set the QEMU variables (QNX_MCP_E2E_TARGET, QNX_MCP_E2E_CREATE, QNX_MCP_E2E_RUN_AS) and/or QNX_MCP_E2E_SSH_HOST; see the top of this script" >&2
    exit 2
fi
: "${QNX_MCP_ALLOW_TARGETS:?set QNX_MCP_ALLOW_TARGETS, for example 'tb,tb[0-9]*' (and 'ami*' for the SSH-only machine)}"
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
    summary_ok "$(tail -n 1 "$log")" || failed=1
}

: > "$log"
{
    echo "qnx-mcp $version, $(git -C "$root" rev-parse --short HEAD), $(date -u +%Y-%m-%dT%H:%MZ)"
    uname -srm
    "$PYTHON" --version
} | tee -a "$log"
suite "$PYTHON" -m pytest -q -p no:asyncio test_units.py test_mcp_offline.py test_skill.py test_nonroot.py test_ssh_target.py
suite "$PYTHON" -m pytest -q -s -p no:asyncio test_build_sdk.py test_boundary_sdk.py
if [ "$qemu" = 1 ]; then
    suite "$PYTHON" -m pytest -q -s -p no:asyncio test_slice_e2e.py test_shellfree_e2e.py
    suite "$PYTHON" -m pytest -q -s -p no:asyncio test_lifecycle_e2e.py
    suite "$PYTHON" -m pytest -q -s -p no:asyncio test_nonroot_e2e.py
fi
if [ "$ssh" = 1 ]; then
    suite "$PYTHON" -m pytest -q -s -p no:asyncio test_ssh_target_e2e.py
fi
echo "log: $log"
exit $failed
