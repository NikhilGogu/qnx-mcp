# gateway

Record path of the field unit, using QNX message passing:

* `feed <n>` sends n records to `gatewayd` and prints `feed: sent <n> stored <n> rejected <n>`.
* `gatewayd [-c conf]` (default `/etc/gateway.conf`) registers `gatewayd` for records and `gatewayd_ctl` for control queries. It forwards each record to `storaged` and returns storaged's answer. Key `quota`: how many records the gateway may have stored (default 1000).
* `storaged [-c conf] [-l log]` (defaults `/etc/storaged.conf`, `/data/var/storaged/records.log`) registers `storaged` and appends each admitted record to the log. Key `quota_check`: `none` (admit everything), `local` (admit up to `local_quota` records, default 100) or `gateway` (before each record, ask gatewayd's control channel how much of the gateway quota is left; admit only while it is above zero). Refused records are answered with EDQUOT and counted as rejected.

Start order: `storaged &`, `gatewayd &`, then `feed`. The log directory must exist. Bench setup: `-c examples/storaged.conf`, `-c examples/gateway.conf`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
