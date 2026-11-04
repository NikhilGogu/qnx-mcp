# sensorhub

Sensor hub for the field unit. It reads one sensor definition per file from a configuration directory (default `/etc/sensor.d`, files `*.cfg`), samples every sensor once per cycle on one worker thread per bus (`i2c0`, `i2c1`, `spi0`, `adc0`), filters the values, computes derived channels from `derived.cfg`, and prints a report after the last cycle. Messages go to stderr and to slog2 (buffer set `sensorhub`).

Usage: `sensorhub [-c confdir] [-n cycles] [-j] [-v]` (defaults: `/etc/sensor.d`, 50 cycles; `-j` prints the report as JSON, `-v` adds information messages and per-sensor trends on stderr). Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment). Example configuration for the bench: `examples/sensor.d` (`sensorhub -c examples/sensor.d`).

## Sensor files

`key = value` lines, `#` starts a comment.

| key | meaning |
|---|---|
| `name` | unique name, at most 31 characters (required) |
| `type` | `temp`, `press`, `humid`, `accel`, `volt` or `ratio` (required) |
| `bus` | `i2c0`, `i2c1`, `spi0` or `adc0` (required); ratio sensors must be on `adc0` |
| `address` | device address (i2c 0..127, spi chip select 0..3, adc input 0..7) |
| `channel` | accel axis 0, 1 or 2 (x, y, z); 0 for every other type |
| `unit` | output unit: C, F, K (temp); hPa, kPa, Pa (press); %RH (humid); g, m/s2 (accel); mV, V (volt); % (ratio) |
| `filter` | `none`, `avg` (moving average), `median`, `iir` (exponential, `alpha` in (0, 1]) or `decim` (block average) |
| `window` | samples for `avg` and `median`, 1..32; block length for `decim`, 1..16 (default 4). A value with an `s` suffix is in seconds and is converted to samples with the bus sampling rate (see buses.conf); the limits apply after conversion |
| `scale`, `offset` | calibration applied to the driver value before filtering |
| `priority` | sampling and report order within a bus, lower first (default 50) |
| `enabled` | 0 skips the sensor (counted as disabled) |
| `ref` | ratio sensors: the reference sensor, which must be an enabled sensor on the same bus |
| `desc` | free text |

A sensor whose file has a bad line or that breaks one of the rules above is rejected: it is not sampled, a warning names the file and the reason, and it is counted in the report header.

## buses.conf

Optional. One line per bus, `<bus> rate_hz=<n>`: the bus sampling rate used to convert windows given in seconds (default 10 Hz).

## derived.cfg

One channel per line, `name = expression`, evaluated in file order once per cycle. An expression is a number, a sensor name, the name of a channel defined on an earlier line, or `sum(...)`, `avg(...)`, `min(...)`, `max(...)` (1 to 8 arguments), `diff(a, b)` or `scale(a, b)` (a times b). Sensor names refer to the filtered value of the current cycle in the sensor's natural unit. An expression nests at most 200 levels deep. The plant model tool writes chains of up to 10000 intermediate channels, each using the one before; channels whose name starts with `_` are intermediate and are not printed in the report (they are counted in the header). A line that does not parse is rejected with a warning.

## alarms.conf

Optional. One rule per line, `<sensor> lo=<v> hi=<v> hyst=<v>` (natural unit; at least one of lo and hi). An alarm is raised when the filtered value leaves the limits and cleared when it is back inside by the hysteresis. The report lists each rule with how often it was raised.

## Report

First line: `sensorhub: <n> sensors, <n> derived, <n> rejected, <n> disabled, <n> cycles`. Then one line per sensor in bus order, then by priority: last, mean, minimum and maximum of the filtered values in the configured unit, the number of filtered values and the number of failed reads. Then one line per derived channel with its value in the last cycle, then one line per alarm rule.
