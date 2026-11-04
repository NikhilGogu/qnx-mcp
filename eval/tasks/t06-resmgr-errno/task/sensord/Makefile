# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: sensord sensorctl

sensord: sensord.o
	$(CC) $(LDFLAGS) -o $@ $^ -lslog2

sensorctl: sensorctl.o
	$(CC) $(LDFLAGS) -o $@ $^

sensord.o sensorctl.o: sensor.h

clean:
	rm -f sensord sensorctl *.o
