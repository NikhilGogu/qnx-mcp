# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64

ledger: ledger.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

clean:
	rm -f ledger *.o
