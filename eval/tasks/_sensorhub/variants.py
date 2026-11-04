#!/usr/bin/env python3
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
"""Generate the three sensorhub v2 tasks from the correct base: each gets one
seeded bug, its own target-side configuration, unseen oracle inputs and
expected outputs computed by a Linux build of the correct base."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "base"
TASKS = HERE.parent
BUILD = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "build-linux"


def cfg(**kv):
    return "".join(f"{k} = {v}\n" for k, v in kv.items())


def unit_set(vib_y_window=24, extra=None, derived_extra="", buses="i2c0 rate_hz=5\ni2c1 rate_hz=2\nspi0 rate_hz=24\nadc0 rate_hz=50\n"):
    s = {
        "buses.conf": buses,
        "10-cab-temp.cfg": cfg(name="cab_temp", type="temp", bus="i2c0", address="0x48", unit="C", filter="avg", window="6s", priority=10, desc="cabinet"),
        "11-out-temp.cfg": cfg(name="out_temp", type="temp", bus="i2c0", address="0x4a", unit="F", filter="median", window=7, priority=11),
        "12-motor-temp.cfg": cfg(name="motor_temp", type="temp", bus="i2c0", address="0x4c", unit="K", filter="iir", alpha=0.3, priority=12),
        "13-oil-temp.cfg": cfg(name="oil_temp", type="temp", bus="i2c0", address="0x4d", unit="C", filter="median", window="4s", priority=13),
        "14-brg-temp.cfg": cfg(name="bearing_temp", type="temp", bus="i2c0", address="0x4e", unit="C", filter="avg", window=32, priority=14),
        "20-baro.cfg": cfg(name="baro", type="press", bus="i2c1", address="0x77", unit="kPa", filter="avg", window="8s", priority=20),
        "21-humid.cfg": cfg(name="humid", type="humid", bus="i2c1", address="0x41", unit="%RH", filter="decim", window="4s", priority=21),
        "22-baro-ref.cfg": cfg(name="baro_ref", type="press", bus="i2c1", address="0x76", unit="hPa", filter="median", window="15s", priority=22),
        "23-humid-out.cfg": cfg(name="humid_out", type="humid", bus="i2c1", address="0x42", unit="%RH", filter="none", priority=23),
        "30-vib-x.cfg": cfg(name="vib_x", type="accel", bus="spi0", address=1, channel=0, unit="g", filter="median", window="0.5s", priority=30),
        "31-vib-y.cfg": cfg(name="vib_y", type="accel", bus="spi0", address=1, channel=1, unit="g", filter="median", window=vib_y_window, priority=31, desc="gearbox, y axis"),
        "32-vib-z.cfg": cfg(name="vib_z", type="accel", bus="spi0", address=1, channel=2, unit="m/s2", filter="avg", window="1s", priority=32),
        "33-hub-x.cfg": cfg(name="hub_x", type="accel", bus="spi0", address=2, channel=0, unit="g", filter="iir", alpha=0.2, priority=33),
        "34-hub-y.cfg": cfg(name="hub_y", type="accel", bus="spi0", address=2, channel=1, unit="g", filter="median", window=31, priority=34),
        "35-hub-z.cfg": cfg(name="hub_z", type="accel", bus="spi0", address=2, channel=2, unit="m/s2", filter="decim", window=16, priority=35),
        "40-vref.cfg": cfg(name="vref_main", type="volt", bus="adc0", address=0, unit="V", filter="avg", window="0.5s", priority=40),
        "41-supply.cfg": cfg(name="supply", type="ratio", bus="adc0", address=1, ref="vref_main", unit="%", filter="avg", window=4, priority=41),
        "42-vref2.cfg": cfg(name="vref_aux2", type="volt", bus="adc0", address=3, unit="mV", filter="median", window="0.6s", priority=42),
        "43-sense-a.cfg": cfg(name="sense_a", type="ratio", bus="adc0", address=4, ref="vref_aux2", unit="%", filter="median", window=9, priority=43),
        "44-sense-b.cfg": cfg(name="sense_b", type="ratio", bus="adc0", address=5, ref="vref_main", unit="%", filter="iir", alpha=0.5, priority=44),
        "45-sense-c.cfg": cfg(name="sense_c", type="ratio", bus="adc0", address=6, ref="vref_aux2", unit="%", filter="none", priority=45),
        "46-rail.cfg": cfg(name="rail_5v", type="volt", bus="adc0", address=7, unit="V", filter="decim", window="0.3s", priority=46),
        "50-spare.cfg": cfg(name="spare", type="temp", bus="i2c1", address="0x30", enabled=0),
        "51-spare2.cfg": cfg(name="spare2", type="accel", bus="spi0", address=3, channel=1, enabled=0),
        "alarms.conf": "cab_temp hi=45 hyst=1\nhumid lo=20 hi=60\nvib_z hi=1.5\nrail_5v lo=2000\n",
        "derived.cfg": "temp_spread = diff(motor_temp, cab_temp)\nshake = max(vib_x, vib_y, hub_x)\nsupply_avg = avg(supply, sense_b)\n" + derived_extra,
    }
    s.update(extra or {})
    return s


def chain(n, final="plant"):
    lines = ["_p1 = avg(cab_temp, oil_temp)"]
    for k in range(2, n + 1):
        lines.append(f"_p{k} = avg(_p{k - 1}, {['cab_temp', 'baro', 'oil_temp'][k % 3]})")
    lines.append(f"{final} = scale(_p{n}, 1.0)")
    return "\n".join(lines) + "\n"


def deep(n):
    e = "0.5"
    for i in range(n):
        e = f"sum({['cab_temp', 'motor_temp', 'baro'][i % 3]}, {e})"
    return e


VARIANTS = {
    "v2-01-config-overflow": {
        "summary": "Median window given in seconds is converted with the unit's bus rate after validation; the 48-sample window overflows a filter buffer and corrupts a neighbouring pointer, and the crash surfaces later in the report",
        "patch": ("src/config.c",
                  """        } else {
            convert_window(c);
            if ((why = validate(c)) != NULL) {
                log_msg(LOG_WARN, "%s: sensor %s rejected: %s", names[i], c->name, why);
                res->rejected++;
            } else {
                res->loaded++;
            }
        }""",
                  """        } else if ((why = validate(c)) != NULL) {
            log_msg(LOG_WARN, "%s: sensor %s rejected: %s", names[i], c->name, why);
            res->rejected++;
        } else {
            convert_window(c);
            res->loaded++;
        }"""),
        "target": unit_set(vib_y_window="2s"),
        "set2": unit_set(vib_y_window=24, buses="i2c0 rate_hz=5\ni2c1 rate_hz=9\nspi0 rate_hz=20\nadc0 rate_hz=12\n", extra={
            "33-hub-x.cfg": cfg(name="hub_x", type="accel", bus="spi0", address=2, channel=0, unit="g", filter="median", window="1.7s", priority=33),
            "34-hub-y.cfg": cfg(name="hub_y", type="accel", bus="spi0", address=2, channel=1, unit="g", filter="median", window="1.6s", priority=34),
            "40-vref.cfg": cfg(name="vref_main", type="volt", bus="adc0", address=0, unit="V", filter="avg", window="3s", priority=40),
            "21-humid.cfg": cfg(name="humid", type="humid", bus="i2c1", address="0x41", unit="%RH", filter="decim", window="2s", priority=21),
            "30-vib-x.cfg": cfg(name="vib_x", type="accel", bus="spi0", address=1, channel=0, unit="g", filter="median", window=0, priority=30),
        }),
        "issue": "sensorhub crashes on the field unit at the end of its run: after the sampling cycles it dies with `Memory fault (core dumped)` and prints no report. On the bench it works with examples/sensor.d. The unit runs it with the default options, so it reads the unit's own configuration in /etc/sensor.d on the target, which we have not changed recently. We build it with `make` in task/sensorhub.",
        "truth_line": "            convert_window(c);",
        "truth_file": "src/config.c", "function": "config_load",
        "mechanism": "config_load() validates a sensor before convert_window() turns a window given in seconds into samples, so the converted value is never range-checked. On the unit, vib_y has filter median and window 2s on spi0, which /etc/sensor.d/buses.conf runs at 24 Hz: 48 samples. filter_apply() then writes the median ring buffer buf[MAX_WINDOW=32] up to index 47, overwriting the unit pointer and stats that follow filt in struct sensor; report_line() later calls unit_convert() through the corrupted pointer and faults in the main thread. Fix: convert before validating (reject the sensor).",
    },
    "v2-04-deep-expr": {
        "summary": "Derived channels that refer to earlier channels are re-evaluated recursively; the unit's 4000-link chain overflows the default worker stack",
        "patch": ("src/expr.c", "    case N_DERIVED:\n        return chans[n->chan].value;", "    case N_DERIVED:\n        return eval(chans[n->chan].root);"),
        "target": unit_set(derived_extra=chain(4000)),
        "set2": unit_set(derived_extra=chain(6000, "plant2") + f"nested = {deep(150)}\ntoo_deep = {deep(201)}\n"),
        "issue": "sensorhub crashes on the field unit right after it starts (`Memory fault (core dumped)`), before it prints anything. On the bench it works with examples/sensor.d. The unit reads its configuration from /etc/sensor.d on the target; the plant model tool updated that configuration last week. We build it with `make` in task/sensorhub.",
        "truth_line": "        return eval(chans[n->chan].root);",
        "truth_file": "src/expr.c", "function": "eval",
        "mechanism": "eval() evaluates a reference to an earlier derived channel by recursing into that channel's expression instead of using the value already computed this cycle. The unit's /etc/sensor.d/derived.cfg chains 4000 intermediate channels (_p1.._p4000, each using the one before), so evaluating them recurses about 8000 frames deep on the derive worker, which has the default 256 KiB QNX thread stack, and overflows it. Fix: use chans[n->chan].value (channels are evaluated in file order); a larger stack alone also stops the crash but keeps the quadratic work.",
    },
    "v2-06-worker-null": {
        "summary": "A ratio sensor whose ref does not resolve (case mismatch among many refs) keeps a NULL pointer that crashes one of several worker threads",
        "patch": ("src/registry.c",
                  "    if (r == NULL || r == s || r->bus != s->bus) {",
                  "    if (r != NULL && (r == s || r->bus != s->bus)) {"),
        "target": unit_set(extra={"47-sense-d.cfg": cfg(name="sense_d", type="ratio", bus="adc0", address=2, ref="Vref_main", unit="%", filter="avg", window=4, priority=47, desc="auxiliary supply")}),
        "set2": unit_set(extra={
            "47-sense-d.cfg": cfg(name="sense_d", type="ratio", bus="adc0", address=2, ref="vref-main", unit="%", priority=47),
            "48-sense-e.cfg": cfg(name="sense_e", type="ratio", bus="adc0", address=2, ref="spare_v", unit="%", priority=48),
            "49-sense-f.cfg": cfg(name="sense_f", type="ratio", bus="adc0", address=2, ref="cab_temp", unit="%", priority=49),
            "52-spare-v.cfg": cfg(name="spare_v", type="volt", bus="adc0", address=5, enabled=0),
        }),
        "issue": "sensorhub crashes on the field unit in its first cycle (`Memory fault (core dumped)`). On the bench it works with examples/sensor.d. The unit reads its configuration from /etc/sensor.d on the target. We build it with `make` in task/sensorhub.",
        "truth_line": "    if (r != NULL && (r == s || r->bus != s->bus)) {",
        "truth_file": "src/registry.c", "function": "bind_ref",
        "mechanism": "bind_ref() rejects a ratio sensor only when its ref resolves to the wrong sensor; when the ref does not resolve at all, the sensor is kept with ref == NULL. On the unit, sense_d refers to Vref_main while the sensor is vref_main (names are case-sensitive), so ratio_read() dereferences s->ref->last_raw in the adc0 worker thread (one of five threads) on the first cycle. Fix: also reject the sensor when the ref is not found.",
    },
}

V204_OLD = ("/* Derived channels: name = expression, where an expression is a number, a\n"
            " * sensor name or fn(expr, ...). Generated plant models nest deeply, so the\n"
            " * worker that parses and evaluates them gets a large stack. */\n#define DERIVE_STACK_KB 16384\n")
V204_NEW = "/* Derived channels: name = expression, where an expression is a number, a\n * sensor name or fn(expr, ...). */\n"


def write_set(d: Path, files: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (d / k).write_text(v)


def run(binary: Path, args: list) -> str:
    p = subprocess.run([str(binary)] + args, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, (args, p.returncode, p.stderr[-400:])
    return p.stdout


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    exe = BUILD / "sensorhub"
    subprocess.run(["gcc", "-std=gnu11", "-O0", "-g", "-o", str(exe)] + sorted(str(p) for p in (BASE / "src").glob("*.c")) + ["-lpthread"], check=True)
    for vid, v in VARIANTS.items():
        t = TASKS / vid
        if t.exists():
            shutil.rmtree(t)
        src = t / "task" / "sensorhub"
        shutil.copytree(BASE, src)
        hid = t / "hidden"
        (hid / "fix" / "src").mkdir(parents=True)
        if True:
            f, old, new = v["patch"]
            p = src / f
            s = p.read_text()
            assert s.count(old) == 1
            p.write_text(s.replace(old, new))
            shutil.copy(BASE / f, hid / "fix" / f)
        write_set(hid / "target" / "etc" / "sensor.d", v["target"])
        write_set(hid / "set2", v["set2"])
        shutil.copytree(BASE / "examples" / "sensor.d", hid / "bench")
        exp = hid / "expected"
        exp.mkdir()
        (exp / "target.out").write_text(run(exe, ["-c", str(hid / "target" / "etc" / "sensor.d")]))
        (exp / "set2.out").write_text(run(exe, ["-c", str(hid / "set2"), "-n", "40"]))
        (exp / "bench.out").write_text(run(exe, ["-c", str(hid / "bench")]))
        lines = (src / v["truth_file"]).read_text().splitlines()
        ln = lines.index(v["truth_line"]) + 1
        (hid / "truth.json").write_text(json.dumps({"file": v["truth_file"], "lines": [ln], "window": 3, "function": v["function"], "mechanism": v["mechanism"]}, indent=2) + "\n")
        (t / "task.json").write_text(json.dumps({"id": vid, "dir": "sensorhub", "summary": v["summary"], "binaries": ["sensorhub"], "issue": v["issue"]}, indent=2) + "\n")
        (hid / "oracle.sh").write_text("""#!/bin/bash
# Copyright 2026 The qnx-mcp-eval authors
# SPDX-License-Identifier: Apache-2.0
# Oracle for %s: the unit's configuration (installed in /etc/sensor.d by the
# harness), an unseen configuration and the bench configuration; exact report.
. ./oracle_lib.sh
build src sensorhub || finish
stage src/sensorhub
scp -r -q "${SSHO[@]}" hidden/set2 hidden/bench "root@$IP:/tmp/oracle/"
NUMTOL=0.0011
expect_out "unit configuration in /etc/sensor.d" 120 0 hidden/expected/target.out 'cd /tmp/oracle && ./sensorhub'
expect_out "unseen configuration" 120 0 hidden/expected/set2.out 'cd /tmp/oracle && ./sensorhub -c /tmp/oracle/set2 -n 40'
expect_out "bench configuration" 120 0 hidden/expected/bench.out 'cd /tmp/oracle && ./sensorhub -c /tmp/oracle/bench'
finish
""" % vid)
        print(vid, "truth", v["truth_file"], ln, "| target.out:", (exp / "target.out").read_text().splitlines()[0])
    shutil.rmtree(BUILD)


if __name__ == "__main__":
    main()
