# pktq

Packet retransmission simulator. It creates `count` packets (default 20) and sends each over a simulated link that fails every `fail_every`-th send (default 4; 0 means lossless). A failed send is retried up to 3 times in total; a packet that still fails is dropped.

Ownership: `link_send()` takes ownership of the packet only when it succeeds (returns 0). When it fails (returns -1) the caller still owns the packet and may retry it or free it.

Output: `pktq: delivered=<n> dropped=<n> sends=<n> failures=<n> checksum=<n>`, where the checksum covers the bytes of every delivered packet. Exit status 0 if nothing was dropped, 1 otherwise.

Usage: `./pktq [count [fail_every]]`. Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
