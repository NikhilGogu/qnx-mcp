# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: acqd acqctl

acqd: acqd.o board.o
	$(CC) $(LDFLAGS) -o $@ $^ -lslog2
acqctl: acqctl.o
	$(CC) $(LDFLAGS) -o $@ $^ -lslog2

acqd.o acqctl.o board.o: acq.h board.h

clean:
	rm -f acqd acqctl *.o
