# Changelog

## Unreleased

* SSH-only targets: `target_register` and `target_unregister` (21 tools) add an existing QNX 8.0 machine that is reachable only over SSH, such as QNX's QNX OS 8.0 AMI on AWS Graviton (aarch64le, user `qnxuser`), as a second target kind. `target_list` and `target_status` show `kind`. Offline tests only; no live AMI result is claimed (`tests/test_ssh_target_e2e.py` is the live suite).
* `QNX_MCP_SSH_HOSTS` (operator allowlist of hosts) decides which machines may be registered; unset refuses all. The host key is pinned on first contact only if it hashes to the given `host_key_fingerprint` (or with `QNX_MCP_TRUST_FIRST_USE=1`), then checked with `StrictHostKeyChecking=yes`. Host-key checking is never disabled.
* `target_start`, `target_stop` and `target_destroy` refuse SSH-only targets with the new error code `target_kind_unsupported`; `target_register` outside the allowlist fails with the new code `ssh_host_not_allowed`.
* `build` takes `target` and builds for that target's architecture; an explicit `arch` that differs is refused with `arch_mismatch`. `arch` now defaults to the target's, else x86_64.
* The login user is taken from the target (`qnxuser` for SSH-only targets): `/tmp/qnx-mcp` ownership checks use `test -O`, `QNX_MCP_RUN_AS` is refused with `config_error` for a non-root login, and `privileges` reports the login user.
* `environment_check` marks the QEMU-only checks as applying to QEMU targets, optional when `QNX_MCP_SSH_HOSTS` is set and no QEMU target exists, and adds `ssh_hosts` and `tool:ssh-keygen`.
* `scripts/live-tests.sh` accepts an SSH-only machine (`QNX_MCP_E2E_SSH_HOST` and `QNX_MCP_E2E_SSH_FINGERPRINT`) and runs the QEMU suites only when their variables are set.
* In qcc mode `build` also checks the files that `-lNAME` finds in a `-L` directory (`libNAME.so`, `libNAME.a`) by content and by real path, because ld reads a text file found there as a linker script.
* An ssh key path (`QNX_MCP_SSH_KEY`) with `%` or `$` is refused before ssh runs and reported by `environment_check`, because ssh expands these in the `-i` file name and would open another file.
* An upload whose scp fails or is cancelled removes its `.part-` file on the target.
* `build` refuses `cflags` and `ldflags` in make mode, where they were checked and then dropped. cmake mode now sets C, C++ and linker flags on every configure, empty when omitted.
* The cmake toolchain file is written to a new file and renamed into place, so a symlink at its name is replaced instead of followed.
* `get_crash` refuses a core with `core_mismatch` when gdb names no executable or no pid for it. Before, a missing value could compare equal to an empty field of the run record.
* The release workflow tests the wheel in a job with a read-only token that checkout does not keep; a separate job with write access only downloads the tested wheel and creates the release.
* `deploy_and_run` and `run_start` refuse arguments with a NUL byte, and arguments or environment entries above 64 KiB in total, with `invalid_argument` before anything is uploaded.
* A state directory with whitespace, quotes, a backslash, `%` or `$` is refused before ssh runs and reported by `environment_check`, because ssh splits or expands that path in `UserKnownHostsFile` and the host-key pin would be lost.
* In qcc mode `build` checks `.o`, `.so` and `.a` inputs by content (ELF or ar) and accepts sources only with a C, C++ or assembly extension, so no input reaches the linker as a script.
* `run_poll` with `wait_s` backs off from one poll every 0.5 s to one every 2 s, so a long wait opens fewer ssh sessions.
* Non-root runs: with `QNX_MCP_RUN_AS=uid:gid` the runner starts the agent's program as that user and group, and a failed drop stops the run before the program starts. Tested on a QNX 8.0 x86_64 QEMU target as uid 1000 (`tests/test_nonroot_e2e.py`). QNX 8.0 answers `fchown` and `fchmod` on a directory descriptor with ENOSYS, so the runner sets the owner and mode of `work` by path and checks the result against the opened directory.
* `environment_check` and `target_status` report `privileges`: the login user, who programs run as, and per tool its identity and what it needs on the target.
* New error code `config_error` for server configuration the agent cannot fix.
* `/tmp/qnx-mcp` and its subdirectories on the target must be real directories owned by root, or the call is refused with `remote_failed`. Each gets one mode on every call, whoever programs run as: `/tmp/qnx-mcp`, `apps`, `apps/<hash>` and `runs` 0711 (other users can pass through but not list them), `bin` and new run directories 0700. Background output files are created 0600.
* The runner makes a core root's and 0600 where dumper wrote it, then moves it into the run directory. Before, the core stayed owned by the program's user (0640), so a process of that user left running could change it at any time before `get_crash`. Where dumper wrote it, the user can still reach it on mkqnximage images (its home's `cores` directory, or `/tmp`); the run result and `get_crash` provenance report that in `core_exposed`.
* `get_crash` reads the build ID of the executable that ran from the core's QNX link map note, so a `remote_binary` run analysed with a different program, or with a binary whose build ID presence differs, is refused with `core_mismatch`. Before, it returned `verified: false` and a backtrace of `??` frames. `provenance.core_build_id_source` says where the build ID came from.
* `target_status` fails with the same error as the other tools when the state directory or key path cannot be passed to ssh, or host keys or the key are missing. Before, it returned `ok: true` with the refusal in its `ssh` field, which now reports only what the target answered.
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
