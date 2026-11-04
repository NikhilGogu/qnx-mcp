# Black-box probes on a QNX 8.0 x86_64 QEMU target (3 Nov 2026)

Small programs of our own (built with `qcc -Vgcc_ntox86_64`, run on a copy of the golden image) used to design the tasks. No QNX binaries were inspected.

| Question | Result |
|---|---|
| Default pthread stack size (`pthread_attr_getstacksize` on a fresh attr) | 262144 bytes |
| Thread touching a local array of 250 KiB / 400 KiB / 1 MiB / 4 MiB | ok / `Memory fault (core dumped)`, exit 139 / 139 / 139 |
| `free()` twice on a 64-byte and a 100000-byte block | `Abort (core dumped)`, exit 134, both sizes |
| Linking a call to `socket()` without `-lsocket` | `undefined reference to 'socket'` |
| devctl handler returning `EINVAL` | client devctl() returns 22 (EINVAL) |
| devctl handler doing `errno = EINVAL; return -1;` | client devctl() returns 0 (EOK) |
| devctl for a command the handler does not know (handler returns ENOSYS) | client devctl() returns 25 (ENOTTY) |
| `MsgSend` with `TimerTimeout(_NTO_TIMEOUT_SEND|_NTO_TIMEOUT_REPLY, 1.5 s)` to a `name_attach` server that never replies | client stays REPLY-blocked (server receives pulse code -32, unblock); no timeout |
| `MsgReceivePulse` with `TimerTimeout(_NTO_TIMEOUT_RECEIVE, 1.5 s)` | returns -1, ETIMEDOUT after 1.50 s |
| `slog2f` from our program, then `slog2info` | entry shown with buffer set name and pid |
