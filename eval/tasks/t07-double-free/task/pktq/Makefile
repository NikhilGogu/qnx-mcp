# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

pktq: pktq.o link.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

pktq.o link.o: packet.h

clean:
	rm -f pktq *.o
