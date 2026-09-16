"""Production app with automation, option recommendation and trade dashboard."""
from __future__ import annotations

from fastapi.responses import FileResponse

from .automation_api import router as automation_router
from .config import PROJECT_ROOT
from .options.recommendation_api import router as option_recommendation_router
from .trade_dashboard_api import router as trade_dashboard_router
from .agents.api import router as agent_lab_router
from .webapp_ops import app
from .mcp_server.http import ResearchMCP
from .research_api import router as research_router

app.include_router(automation_router)
app.include_router(option_recommendation_router)
app.include_router(trade_dashboard_router)
app.include_router(agent_lab_router)
app.include_router(research_router)

# Separate read-only network facade; never mount the legacy report-writing MCP.
app.mount("/mcp", ResearchMCP(app), name="research-mcp")


@app.get("/trades")
def trade_dashboard_page() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "webui" / "trades.html")
