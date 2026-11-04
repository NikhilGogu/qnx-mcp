# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: statusd statusctl

statusd: statusd.o parsers.o
	$(CC) $(LDFLAGS) -o $@ $^
statusctl: statusctl.o
	$(CC) $(LDFLAGS) -o $@ $^

statusd.o statusctl.o parsers.o: status.h parsers.h

clean:
	rm -f statusd statusctl *.o
