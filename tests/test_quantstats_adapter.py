"""Optional dependency test: use actual pinned QuantStats with external I/O blocked."""
import socket
from datetime import date, timedelta
import pytest


def test_actual_quantstats_reconciles_offline(monkeypatch):
    qs = pytest.importorskip("quantstats")
    import yfinance
    from stock_machine.integrations.reporting import render_report
    def no_network(*a, **kw):
        raise AssertionError("REPORT_ATTEMPTED_NETWORK_ACCESS")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(yfinance, "download", no_network)
    monkeypatch.setattr(qs.utils, "download_returns", no_network)
    values, equity = [], 1000.
    for i in range(60):
        equity *= 1 + (.002 if i%3 else -.003)
        values.append({"session": (date(2020,1,1)+timedelta(days=i)).isoformat(), "equity_usd": str(equity),
                       "external_flows_usd": "1000", "status": "COMPLETE"})
    snapshot = {"schema_version": "performance-input.v1", "portfolio_id": "test", "quality": "PROSPECTIVE_SIMULATION",
                "valuations": values, "sessions": [r["session"] for r in values], "benchmark": []}
    result = render_report(snapshot)
    assert result["quantstats"]["status"] == "OK"
    assert result["quantstats"]["total_return"] == pytest.approx(equity/1000-1)
    assert result["quantstats"]["annualized_volatility"] == pytest.approx(result["series"]["metrics"]["annualized_volatility"])
    assert result["quantstats"]["sharpe_rf_zero"] is not None
