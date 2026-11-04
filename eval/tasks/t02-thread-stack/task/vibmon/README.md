# vibmon

Vibration monitor test tool. It generates a deterministic pseudo-random vibration signal (seeded), analyses the last 65536 samples on a worker thread and prints the RMS and peak amplitude.

Usage: `./vibmon [seed [samples]]` (defaults: seed 42, 65536 samples).

Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
