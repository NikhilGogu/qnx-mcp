/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef GW_H
#define GW_H

#include <stdint.h>
#include <sys/iomsg.h>

#define GW_NAME     "gatewayd"
#define GW_CTL_NAME "gatewayd_ctl"
#define STOR_NAME   "storaged"

#define MSG_RECORD (_IO_MAX + 0x21)
#define MSG_STORE  (_IO_MAX + 0x22)
#define MSG_QUOTA  (_IO_MAX + 0x23)
#define MSG_STATS  (_IO_MAX + 0x24)

struct record_msg {
    uint16_t type;
    uint16_t zero;
    uint32_t seq;
    char payload[48];
};

struct store_reply {
    int32_t status;
    uint32_t stored;
};

struct quota_msg {
    uint16_t type;
    uint16_t zero;
    uint32_t seq;
};

struct quota_reply {
    int32_t remaining;
};

struct stats_reply {
    uint32_t stored;
    uint32_t rejected;
};

#endif
