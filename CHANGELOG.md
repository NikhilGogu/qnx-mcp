# Changelog

## Unreleased

* Non-root runs: with `QNX_MCP_RUN_AS=uid:gid` the runner starts the agent's program as that user and group, and a failed drop stops the run before the program starts. Not yet tested on a target (`tests/test_nonroot_e2e.py`).
* `environment_check` and `target_status` report `privileges`: the login user, who programs run as, and per tool its identity and what it needs on the target.
* New error code `config_error` for server configuration the agent cannot fix.
* `build` refuses compiler, linker and make arguments outside an allow-list, and host paths are always confined to the workspace (the server's working directory when `QNX_MCP_WORKSPACE` is not set).
* The offline tests run in GitHub Actions on Python 3.10 to 3.13. They no longer read host files, so they also pass on macOS.

## 0.3.0 (4 November 2026)

* `process_stop` reads the executable from `/proc/<pid>/exefile`, so services started with a relative path can be stopped.
* `remote_binary` starts programs only below `QNX_MCP_REMOTE_BINARY_ROOTS` (default `/tmp` and `/data`), never a shell or a system program. The runner checks the real path on the target.
* The evaluation study is in `eval/`.

## 0.2.1 (3 November 2026)

* `process_stop` checks the process start time as a creation token, again before each signal, and reports an unreadable state as an error.
* `file_list` reports `complete` and `real_path_verified`.
* Crash provenance for `remote_binary` runs uses the build ID only.
* Target addresses are rediscovered once after a failed connection, without port probes.

## 0.2.0 (3 November 2026)

* New tools for agents without a shell on the target: `file_list`, `process_stop`, and `remote_binary` for `deploy_and_run` and `run_start`.
* The agent skill covers them.

## 0.1.0 (3 November 2026)

* First release: target lifecycle for mkqnximage QEMU targets, `build`, `deploy_and_run`, background runs, `get_crash` with provenance checks, file transfer, `processes`, `logs` and `environment_check`.
* `qnx-mcp --check` runs the environment checks from a terminal.
* The codelab "Give your AI coding agent a QNX target" and an agent skill.
