# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

pipeline: pipeline.o queue.o conf.o
	$(CC) $(LDFLAGS) -o $@ $^

pipeline.o queue.o conf.o: queue.h conf.h

clean:
	rm -f pipeline *.o
