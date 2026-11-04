/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef LOG_H
#define LOG_H

enum { LOG_ERR, LOG_WARN, LOG_INFO, LOG_DEBUG };

void log_init(const char *name, int verbose);
void log_msg(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void log_close(void);

#endif
