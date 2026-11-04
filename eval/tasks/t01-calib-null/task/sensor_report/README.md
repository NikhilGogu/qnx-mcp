# sensor_report

Reads a sensor configuration file (default `sensors.cfg`): one sensor per line, its name followed by up to 16 raw integer samples. Lines starting with `#` are ignored.

For each sensor it prints the name and the average of its calibrated samples with two decimals. A calibrated sample is `raw * gain + offset`, using the sensor's calibration table. Sensors that have no calibration table are reported with their raw average (gain 1, offset 0) and the marker `(uncalibrated)`. The last line is the number of sensors.

Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment). Run: `./sensor_report [config]`.
