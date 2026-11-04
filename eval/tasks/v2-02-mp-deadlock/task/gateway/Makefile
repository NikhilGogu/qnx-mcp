# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: gatewayd storaged feed

gatewayd: gatewayd.o conf.o
	$(CC) $(LDFLAGS) -o $@ $^
storaged: storaged.o conf.o
	$(CC) $(LDFLAGS) -o $@ $^
feed: feed.o
	$(CC) $(LDFLAGS) -o $@ $^

gatewayd.o storaged.o feed.o conf.o: gw.h conf.h

clean:
	rm -f gatewayd storaged feed *.o
