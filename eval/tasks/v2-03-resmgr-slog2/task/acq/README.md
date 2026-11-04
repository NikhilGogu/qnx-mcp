# acq

Data acquisition front end of the field unit.

* `acqd [-b board]` is a resource manager for `/dev/acq0`. It reads the board description written at manufacturing (default `/etc/acq.board`, including the board revision `rev`) and logs to slog2 (buffer set `acqd`). Rev C boards are set through their registers directly; rev E boards set rate, gain and trigger through a window comparator. The devctl contract below is the same for every revision.
* `acqctl apply [conf]` applies the acquisition configuration (default `/etc/acq.conf`): it stops acquisition, sets rate, gain, coupling and trigger, and starts again. It runs from the unit's start-up script, so it reports details only in slog2 (buffer set `acqctl`) and prints `acqctl: configuration applied` when it is done. `acqctl status` prints the current state.

## devctl contract of /dev/acq0 (acq.h)

| command | EOK | EINVAL | ERANGE | EBUSY | ENOTSUP |
|---|---|---|---|---|---|
| SET_RATE (Hz) | applied | 0 or not a multiple of 10 | above the board's max_rate_hz | acquisition running | |
| SET_GAIN | applied | not 1, 2, 4, 8, 16 or 32 | above the board's max_gain | acquisition running | |
| SET_COUPLING | applied | not 0 (dc) or 1 (ac) | | acquisition running | ac on a board without ac coupling |
| SET_TRIGGER (mV) | trigger armed at that level | | level outside the board's trig_min_mv..trig_max_mv | | board without a trigger input |

acqctl handles the answers as the contract intends: ERANGE means the value is clamped to the board limit and set again; ENOTSUP means the feature is missing on this board, so the setting is skipped (for the trigger: the trigger is turned off).

Bench setup: `acqd -b examples/acq.board &` then `acqctl apply examples/acq.conf`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
