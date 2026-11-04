# sensord

`sensord` is a resource manager that serves `/dev/sensor0` and logs to slog2 (buffer set `sensord`). Start it with `./sensord &`.

Interface (`sensor.h`):

* `DCMD_SENSOR_SET_RATE` (devctl, `uint32_t` in Hz): supported rates are 1, 10, 50 and 100 Hz. devctl() returns EOK when the rate is applied and EINVAL when the rate is not supported; the current rate is then unchanged.
* `DCMD_SENSOR_GET_RATE` (devctl, `uint32_t` out): the current rate. The default is 10 Hz.

`sensorctl`:

* `./sensorctl get-rate` prints `rate: <n> Hz`.
* `./sensorctl set-rate <hz>` requests the rate. If sensord rejects it with EINVAL, sensorctl falls back to the highest supported rate below the request (at least 1 Hz) and prints `rate: <actual> Hz (requested <hz>)`; otherwise it prints `rate: <hz> Hz`.

Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
