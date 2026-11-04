# pipeline

Record pipeline of the field unit: a reader thread parses `id,value` lines from the input file, `workers` transform threads compute `value * 3 + 7`, and a writer thread appends the records to the output file in batches of `batch` records (the last batch of a run may be shorter). Lines that do not parse, or that have a negative id, are counted as bad and skipped. At the end it prints `pipeline: read <n> written <n> bad <n> batches <n>` and exits 0 if every record read was written.

Usage: `pipeline [conf]` (default `/etc/pipeline.conf`). Keys: `input`, `output` (defaults under `/data/var/pipeline`), `batch` (default 1), `queue_depth` (default 64, at least `batch`), `rate_limit_ms` (pause after every 100 input lines, default 0), `workers` (transform threads, 1 to 8, default 1). With several workers the output order may differ from the input order. Bench run: `pipeline examples/pipeline.conf`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
