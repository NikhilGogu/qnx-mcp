/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CFGSVC_H
#define CFGSVC_H

#include <stdint.h>
#include <sys/iomsg.h>

#define CFGSVC_NAME  "cfgsvc"
#define CFG_MSG_TYPE (_IO_MAX + 0x10)

enum { CFG_GET = 1, CFG_SET = 2 };

struct cfg_msg {
    uint16_t type;
    uint16_t op;
    char key[32];
    char value[64];
};

struct cfg_reply {
    char value[64];
};

#endif
