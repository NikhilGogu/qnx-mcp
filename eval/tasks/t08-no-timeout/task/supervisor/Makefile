# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: supervisor worker

supervisor: supervisor.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

worker: worker.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

supervisor.o worker.o: ready.h

clean:
	rm -f supervisor worker *.o
