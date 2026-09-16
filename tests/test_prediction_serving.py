from stock_machine import webapp, research_contract
from stock_machine.prediction import MODEL_VERSION
from tests.test_research_integrity import source_bundle


def bind(monkeypatch, payload, price_date="2026-08-20"):
    bundle = {"company": {"ticker": "AAPL"}, "market_snapshot": {"price_date": price_date}}
    monkeypatch.setattr(research_contract, "read_inputs", lambda t: (bundle, None, payload))
    monkeypatch.setattr(research_contract, "latest_completed_session", lambda *a: "2026-08-20")


def test_prediction_endpoint_is_read_only_and_pending(monkeypatch):
    bind(monkeypatch, None)
    result = webapp.predict("aapl")
    assert result["status"] == "PENDING"
    assert result["ticker"] == "AAPL"
    assert result["research_contract"]["schema_version"] == "research-contract.v1"


def test_prediction_endpoint_refuses_stale_vintage(monkeypatch):
    bind(monkeypatch, {"status": "OK", "ticker": "AAPL", "as_of": "2026-08-19", "model_version": MODEL_VERSION})
    result = webapp.predict("AAPL")
    assert result["status"] == "STALE"
    assert result["latest_price_date"] == "2026-08-20"


def test_current_computation_is_not_evidence_of_qualified_forecast(monkeypatch):
    payload = {"status": "OK", "ticker": "AAPL", "as_of": "2026-08-20", "model_version": MODEL_VERSION,
               "models": {"invented": {"expected_return": 999}}, "forecast_distribution": {"probability_up": 1}}
    bind(monkeypatch, payload)
    result = webapp.predict("AAPL")
    assert result["status"] == "WITHHELD"
    assert "models" not in result and "forecast_distribution" not in result
    assert not result["research_contract"]["guidance_eligible"]


def test_all_forecast_consumers_withhold_before_provider_calls(monkeypatch, source_bundle):
    from stock_machine import webapp_ops, api_v1, market_data
    from stock_machine.research_cycle import build_brief
    from tests.test_research_integrity import NOW
    brief = build_brief(source_bundle, now=NOW)
    payload = {"status": "OK", "ticker": "VZ", "as_of": "2026-09-15", "model_version": MODEL_VERSION,
               "horizons": {"12m": {"expected_return_pct": 999, "prob_positive": 1}}}
    monkeypatch.setattr(research_contract, "latest_completed_session", lambda *a: "2026-09-15")
    monkeypatch.setattr(research_contract, "read_inputs", lambda t: (source_bundle, brief, payload))
    monkeypatch.setattr(api_v1, "read_inputs", research_contract.read_inputs)
    def forbidden():
        raise AssertionError("unqualified guidance triggered a paid provider read")
    monkeypatch.setattr(market_data, "get_provider", forbidden)
    views = [webapp.predict("VZ"), webapp_ops.p1_decision_intelligence("VZ"),
             webapp.option_scan("VZ", "OCT26", "bear_put_spread", "40,45", objective="expected_value"),
             webapp.option_generate("VZ", "OCT26", "40,45")]
    assert all(v["status"] == "WITHHELD" for v in views)
    assert len({v["research_contract"]["snapshot_id"] for v in views}) == 1
    packet = api_v1.stock_research("VZ", include_live_quote=False)
    assert packet["model_distribution"]["three_month"] is None
    assert packet["model_distribution"]["twelve_month"] is None
    assert packet["decision_context"]["expected_return_12m_pct"] is None
