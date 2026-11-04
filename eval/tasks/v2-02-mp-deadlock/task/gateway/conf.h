/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#ifndef CONF_H
#define CONF_H

int conf_get(const char *path, const char *key, char *out, int outlen);
long conf_get_long(const char *path, const char *key, long dflt);

#endif
