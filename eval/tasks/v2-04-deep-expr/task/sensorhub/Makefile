# SPDX-License-Identifier: Apache-2.0
CC      = qcc
CFLAGS  = -Vgcc_ntox86_64 -g -O0 -Wall -Wextra -std=gnu11
LDFLAGS = -Vgcc_ntox86_64
LDLIBS  = -lslog2
OBJS    = src/main.o src/config.o src/registry.o src/drivers.o src/bus.o src/worker.o src/filter.o \
          src/stats.o src/units.o src/sim.o src/expr.o src/report.o src/log.o src/util.o \
          src/alarms.o src/trend.o src/json.o src/selftest.o

sensorhub: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)

$(OBJS): $(wildcard src/*.h)

clean:
	rm -f sensorhub $(OBJS)
