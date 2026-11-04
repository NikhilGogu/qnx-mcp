# cfgsvc

A small configuration service using QNX native message passing.

* `cfgsrv` registers the name `cfgsvc` with `name_attach()` and keeps three keys: `rate` (50), `mode` (auto), `gain` (2.5).
* `cfgctl get <key>` prints `<key>=<value>`. `cfgctl set <key> <value>` changes a key and prints the new value. For a key that does not exist, both print `cfgctl: no such key: <key>` on stderr and exit with status 2.

Start the server first (`./cfgsrv &`), then use cfgctl. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
