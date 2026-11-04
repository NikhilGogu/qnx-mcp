# supervisor

Start-up supervisor. `./supervisor [config]` starts `./worker` (from the current directory) with the given configuration file (default `worker.conf`) and waits for the worker's ready pulse on its channel. When the worker is ready it prints `supervisor: worker ready`, waits for the worker to exit and prints its exit status.

Requirement: if the worker has not reported ready within 2 seconds, supervisor prints `supervisor: worker not ready after 2 s`, kills the worker if it is still running and exits with status 3.

Worker configuration keys: `startup_delay_ms` (time before reporting ready, default 0) and `work_ms` (time spent working after ready, default 200).

Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
