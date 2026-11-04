# telemetry

Telemetry sender and receiver, built with CMake from four directories: `libs/core` (CRC-32, ring buffer, clock, hex dump), `libs/net` (UDP helpers), `apps/telemd` (samples a value and sends `count` packets over UDP) and `tools/tlmdump` (receives and checks them).

* `telemd [-p [host:]port] [-n count] [-i interval_ms]` prints `telemd: sent <n> packets to port <port>`.
* `tlmdump [-p [host:]port] [-n count] [-t timeout_ms] [-v]` prints `tlmdump: received <n> packets, <n> bad, seq <first>..<last>` and exits 0 when all packets arrived intact.

Linux build: `cmake -S . -B build && cmake --build build`. QNX OS 8.0 x86_64 cross build (after `source ~/qnx800/qnxsdp-env.sh`): `cmake -S . -B build-qnx -DCMAKE_TOOLCHAIN_FILE=cmake/qnx-x86_64.cmake && cmake --build build-qnx`. Both builds must keep working. On the unit, telemd and tlmdump are installed as single files (copied to the target on their own), and packets are little-endian on the wire.
