/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef STATUS_H
#define STATUS_H

#include <stdint.h>
#include <sys/iomsg.h>

#define STATUS_NAME "statusd"
#define MSG_STATUS  (_IO_MAX + 0x31)

struct status_msg {
    uint16_t type;
    uint16_t zero;
    char component[32];
};

struct status_reply {
    int32_t err;
    char text[120];
};

#endif
