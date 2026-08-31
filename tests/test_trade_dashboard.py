from fastapi.testclient import TestClient

import stock_machine.trade_dashboard as dashboard


def test_rank_opportunities_orders_bull_and_bear():
    research = {
        "AAA": {"sector": "Tech", "report_12m": {"expected_return_pct": 20, "classification": "ATTRACTIVE"}},
        "BBB": {"sector": "Tech", "report_12m": {"expected_return_pct": -30, "classification": "UNATTRACTIVE"}},
        "CCC": {"sector": "Retail", "report_12m": {"expected_return_pct": 8, "classification": "WATCH"}},
    }
    ranked = dashboard._rank_opportunities(research)
    assert ranked["bullish"][0]["ticker"] == "AAA"
    assert ranked["bearish"][0]["ticker"] == "BBB"


def test_dashboard_api_returns_aggregated_state(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "build_dashboard",
        lambda: {
            "status": "OK",
            "portfolio": {"status": "PENDING", "positions": []},
            "opportunities": {"bullish": [], "bearish": []},
            "strategy_lab": {"status": "PENDING"},
            "forward_paper": {"status": "PENDING", "cohorts": []},
            "automation": {"status": "OK"},
        },
    )
    from stock_machine.webapp_automation import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/trade-dashboard")
    assert response.status_code == 200
    assert response.json()["status"] == "OK"


def test_trades_page_is_served():
    from stock_machine.webapp_automation import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/trades")
    assert response.status_code == 200
    assert "Trade Decision Dashboard" in response.text
