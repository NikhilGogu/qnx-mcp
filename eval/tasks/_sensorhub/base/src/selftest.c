/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include "drivers.h"
#include "log.h"
#include "registry.h"
#include "sim.h"
#include "units.h"

/* Start-up checks that do not touch the sensors' state: the simulated bus
 * answers inside the 12-bit range, every sensor has a driver and a unit of
 * the right quantity, and unit conversion round-trips. */
int selftest_run(void)
{
    int problems = 0;

    for (int bus = 0; bus < NBUSES; bus++) {
        int raw = sim_raw(bus, 1, 0, 0);

        if (raw < 0 || raw > 4095) {
            log_msg(LOG_ERR, "selftest: bus %s returned %d", bus_name(bus), raw);
            problems++;
        }
    }
    for (int i = 0; i < registry_count(); i++) {
        const struct sensor *s = registry_get(i);

        if (s->ops == NULL || s->ops->type != s->type) {
            log_msg(LOG_ERR, "selftest: sensor %s has no %s driver", s->name, type_name(s->type));
            problems++;
        }
        if (s->unit == NULL || s->unit->quantity != s->type) {
            log_msg(LOG_ERR, "selftest: sensor %s has no usable unit", s->name);
            problems++;
            continue;
        }
        if (s->unit->scale == 0.0) {
            log_msg(LOG_ERR, "selftest: unit %s cannot be inverted", s->unit->name);
            problems++;
        }
    }
    if (problems == 0)
        log_msg(LOG_INFO, "selftest passed for %d sensors", registry_count());
    return problems;
}
