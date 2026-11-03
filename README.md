# qnx-mcp

An MCP (Model Context Protocol) server that gives AI coding agents structured, bounded tools for QNX OS 8.0 development: boot QEMU targets with mkqnximage, build with the QNX SDP toolchain, deploy and run with timeouts, turn dumper core files into source-level backtraces, and read `pidin` and `slog2` as JSON. Design, security model and roadmap: [docs/DESIGN.md](docs/DESIGN.md).

## Requirements

* QNX SDP 8.0 (tested with the non-commercial licence; `qnxsdp-env.sh`, qcc, mkqnximage, ntox86_64-gdb).
* Linux x86_64 host with QEMU and KVM (tested on Ubuntu 22.04 with libvirt's `virbr0` network), ssh/scp, an ed25519 key.
* Python 3.10+.
* Targets: QNX OS 8.0 x86_64 QEMU images built by mkqnximage (the server builds them for you).

## Install and run

```sh
python3 -m venv ~/venv-qnx-mcp
~/venv-qnx-mcp/bin/pip install -e '.[test]'
~/venv-qnx-mcp/bin/qnx-mcp            # MCP over stdio; normally started by your agent
```

Configuration is by environment: `QNX_MCP_SDP_ENV` (default `~/qnx800/qnxsdp-env.sh`), `QNX_MCP_TARGETS_DIR` (default `~/.qnx-mcp/targets`), `QNX_MCP_STATE_DIR`, `QNX_MCP_ALLOW_TARGETS` (comma-separated globs, default `*`), `QNX_MCP_SSH_KEY` (default `~/.ssh/id_ed25519`), `QNX_MCP_DHCP_LEASES`.

Claude Code: `claude mcp add qnx -e QNX_MCP_ALLOW_TARGETS='dev*' -- ~/venv-qnx-mcp/bin/qnx-mcp`. Codex, Cursor and VS Code: see docs/DESIGN.md section 6.

## Tools

`environment_check`, `target_list`, `target_create`, `target_start`, `target_stop`, `target_status`, `build`, `deploy_and_run`, `get_crash`, `processes`, `logs`. Each returns JSON with `ok`, or `ok: false` with `error {code, message, hint}`.

## Tests

```sh
cd tests
python -m pytest -q -p no:asyncio test_units.py test_mcp_offline.py   # no QNX needed
python -m pytest -q -p no:asyncio test_build_sdk.py                                  # needs QNX SDP
QNX_MCP_E2E_TARGET=tb python -m pytest -q -s -p no:asyncio test_slice_e2e.py         # needs a running target
QNX_MCP_E2E_CREATE=tb2 python -m pytest -q -s -p no:asyncio test_lifecycle_e2e.py    # builds and boots a new target
```

The slice test drives the server through an MCP `ClientSession` over stdio: build `examples/crash_demo`, run it (SIGSEGV), then `get_crash` must name `read_calibration` in `crash.c` at the marked line.

## Licence

Apache-2.0, see [LICENSE](LICENSE).
