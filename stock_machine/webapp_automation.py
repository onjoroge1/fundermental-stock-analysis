"""Production app with PR34 control-plane automation routes."""
from __future__ import annotations

from .automation_api import router as automation_router
from .webapp_ops import app

app.include_router(automation_router)
