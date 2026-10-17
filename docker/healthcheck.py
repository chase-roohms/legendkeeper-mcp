"""Container health check: probes /healthz in HTTP mode; stdio mode has nothing to probe."""

import os
import sys
import urllib.request

if os.environ.get("MCP_TRANSPORT", "stdio").lower() not in ("http", "streamable-http"):
    sys.exit(0)
port = os.environ.get("MCP_PORT", "8000")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except OSError:
    sys.exit(1)
