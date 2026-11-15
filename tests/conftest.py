# Copyright 2026 The qnx-mcp authors
# SPDX-License-Identifier: Apache-2.0
"""Host paths must be inside the workspace. The SDK and end-to-end tests build this repository's examples
and write to pytest's temporary directories, so unless the caller set QNX_MCP_WORKSPACE, both are the
workspace for the test run (and for the servers the tests start, which inherit it)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QNX_MCP_WORKSPACE", os.pathsep.join([str(Path(__file__).resolve().parents[1]), os.path.realpath(tempfile.gettempdir())]))
