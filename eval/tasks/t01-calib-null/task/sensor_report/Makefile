# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

sensor_report: main.o calib.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

main.o calib.o: calib.h

clean:
	rm -f sensor_report *.o
