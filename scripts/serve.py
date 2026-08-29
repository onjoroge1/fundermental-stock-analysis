"""Launch the dashboard on the port the environment assigns.

uvicorn does not read $PORT itself, so removing a hardcoded --port flag would
silently fall back to 8000. This shim honours PORT (what the preview harness
sets) and falls back to 8642 for manual runs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "stock_machine.webapp_ops:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8642")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
