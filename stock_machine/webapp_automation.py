"""Production app with automation, recommendation and owner observability."""
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
from .admin_panel.api import router as admin_panel_router
from .integrations.performance_api import router as performance_router

app.include_router(automation_router)
app.include_router(option_recommendation_router)
app.include_router(trade_dashboard_router)
app.include_router(agent_lab_router)
app.include_router(research_router)
app.include_router(admin_panel_router)
app.include_router(performance_router)
app.mount("/mcp", ResearchMCP(app), name="research-mcp")


@app.get("/trades")
def trade_dashboard_page() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "webui" / "trades.html")
