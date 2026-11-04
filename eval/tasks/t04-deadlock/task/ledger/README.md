# ledger

Two worker threads move money between two accounts: worker 0 moves 7 from a0 to a1, worker 1 moves 3 from a1 to a0, each `n` times (default 500). Every balance change is journalled. At the end it prints both balances, their total (always 200000) and the number of journal entries (two per transfer).

Usage: `./ledger [n]`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
