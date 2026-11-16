# Changelog

## Unreleased

* `build` refuses `cflags` and `ldflags` in make mode, where they were checked and then dropped. cmake mode now sets C, C++ and linker flags on every configure, empty when omitted.
* The cmake toolchain file is written to a new file and renamed into place, so a symlink at its name is replaced instead of followed.
* `get_crash` refuses a core with `core_mismatch` when gdb names no executable or no pid for it. Before, a missing value could compare equal to an empty field of the run record.
* The release workflow tests the wheel in a job with a read-only token that checkout does not keep; a separate job with write access only downloads the tested wheel and creates the release.
* `deploy_and_run` and `run_start` refuse arguments with a NUL byte, and arguments or environment entries above 64 KiB in total, with `invalid_argument` before anything is uploaded.
* `build` accepts two-element flag forms (`-I dir`, `-D NAME`), `-isystem`, `-iquote` and `-include` with workspace paths, `-Wl,-Map` with a workspace path, prefix maps, `PREFIX` and `DESTDIR` inside the workspace, and in qcc mode `-D` values that are double-quoted strings without spaces.
* `build` refuses compiler, linker and make arguments outside an allow-list, and host paths are always confined to the workspace (the server's working directory when `QNX_MCP_WORKSPACE` is not set).
* A tag `v<version>` builds the wheel, tests the installed wheel and attaches it to a GitHub Release (`.github/workflows/release.yml`).
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
