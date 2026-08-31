"""Production app with control-plane automation and option recommendation routes."""
from __future__ import annotations

from .automation_api import router as automation_router
from .options.recommendation_api import router as option_recommendation_router
from .webapp_ops import app

app.include_router(automation_router)
app.include_router(option_recommendation_router)
