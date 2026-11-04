# udpecho

UDP echo test tool on the loopback interface.

* `./udpecho serve <port>`: answers each datagram with the same text in upper case. A datagram `QUIT` is answered with `BYE` and stops the server, which then prints how many messages it served.
* `./udpecho send <port> <message>`: sends one datagram, prints the reply, exits 1 if no reply arrives within 2 s.

Build for QNX 8.0 x86_64 with `make` (needs the QNX SDP environment).
