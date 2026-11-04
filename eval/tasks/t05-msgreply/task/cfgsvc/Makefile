# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

all: cfgsrv cfgctl

cfgsrv: cfgsrv.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

cfgctl: cfgctl.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

cfgsrv.o cfgctl.o: cfgsvc.h

clean:
	rm -f cfgsrv cfgctl *.o
