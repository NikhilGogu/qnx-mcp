# status

Component status service of the field unit, using QNX message passing.

* `statusd [-d dir]` (default `/data/var/status`) registers `statusd`. Each component has a state file `<dir>/<component>.state` written by the component itself, in one of three formats: `key=value` lines; a `#v2` header followed by a `[status]` section of `key = value` lines; or a single legacy line `STATE <state> <since> <code>`. Keys: `state` (required), `since` (epoch seconds), `code`. A component that keeps versioned state files makes `<component>.state` a symbolic link to the current version; the answer then ends with the name of that file in brackets. statusd limits itself to 48 open files.
* `statusctl <component>` prints `<component>: <state> since <since> code <code>`. Errors: `no such component` (exit 2), `malformed state file` (exit 3), `internal error (<reason>)` (exit 1).

The unit's monitor polls every component once a second. Bench run: `statusd -d examples/status &` then `statusctl fan`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
