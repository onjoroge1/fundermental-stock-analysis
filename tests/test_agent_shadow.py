from datetime import datetime, timezone
import pytest
from stock_machine.agent_intelligence import shadow
from stock_machine.market_calendar import session_offset, session_dates


def snapshot(**overrides):
    return {
        "ticker": "VZ",
        "decision_id": "d",
        "origin_session": "2026-09-01",
        "due_session": session_offset("2026-09-01", 5),
        "first_future_session": "2026-09-02",
        "horizon_sessions": 5,
        "components": {"technical": 0.5},
        "forecasts": {
            "forecast:lstm:v1": {
                "prob_positive": 0.7,
                "origin_adjusted_price": 100,
                "p10": 90,
                "p50": 105,
                "p90": 120,
            }
        },
        "candidate_score": 0.5,
        "baseline_score": -0.5,
        "agent_action": "NO_TRADE",
        "weight_version": "frozen",
        **overrides,
    }


def path(s, last=110):
    days = session_dates(s["origin_session"], s["due_session"])
    return [{"date": d, "adj_close": 100 if d != days[-1] else last} for d in days]


def flat_market(s):
    return path(s, 100)


def score(s, rows=None, market=None, completed=None):
    return shadow.evaluate(
        s,
        path(s) if rows is None else rows,
        completed=completed or s["due_session"],
        market_prices=flat_market(s) if market is None else market,
    )


def test_shadow_scores_frozen_forecast_and_baseline_without_trade():
    s = snapshot()
    result = score(s)
    assert result["candidate_error"] < result["baseline_error"]
    assert result["candidate_hit"] is True and result["baseline_hit"] is False
    assert result["agent_action_hit"] is None
    model = result["forecast_scores"]["forecast:lstm:v1"]
    assert model["brier"] == pytest.approx(0.09)
    assert model["median_abs_return_error_pct"] == pytest.approx(5)
    assert model["interval_80_covered"] is True
    assert result["promotion"] == "NOT_AUTHORIZED"
    assert result["broker_submission"] is False


@pytest.mark.parametrize("invalid", [None, 0, float("nan"), float("inf"), True])
def test_shadow_never_falls_back_to_unadjusted_prices(invalid):
    s = snapshot()
    rows = path(s)
    rows[1].update(adj_close=invalid, close=100)
    with pytest.raises(ValueError, match="PATH_INCOMPLETE"):
        score(s, rows)


def test_missing_middle_session_and_unmatured_target_are_rejected():
    s = snapshot()
    with pytest.raises(ValueError, match="NOT_MATURE"):
        score(s, completed=s["origin_session"])
    with pytest.raises(ValueError, match="ADJUSTED_PATH_INCOMPLETE"):
        score(s, path(s)[1:])
    with pytest.raises(ValueError, match="MARKET_PATH_INCOMPLETE"):
        score(s, market=flat_market(s)[1:])


def test_zero_return_is_not_called_a_successful_direction():
    s = snapshot()
    result = score(s, path(s, 100))
    assert result["candidate_hit"] is None
    assert result["forecast_scores"]["forecast:lstm:v1"]["brier"] is None


def test_more_forecast_models_do_not_increase_family_budget():
    a = shadow.candidate_weights([], {"fundamental": 1, "forecast:a:v1": 1}, "VZ", 5)
    b = shadow.candidate_weights(
        [], {"fundamental": 1, "forecast:a:v1": 1, "forecast:b:v1": 1}, "VZ", 5
    )
    assert sum(b["weights"].values()) == pytest.approx(1)
    assert a["weights"]["fundamental"] == b["weights"]["fundamental"] == 0.5
    assert b["status"] == "COLD_START"


def outcome(ticker, horizon, values, z):
    return {"ticker": ticker, "horizon_sessions": horizon, "target": shadow.TARGET,
            "component_values": values, "residual_z": z}


def test_weights_pool_stock_evidence_but_isolate_horizon_and_model_version():
    history = [
        outcome("MSFT", 5, {"technical": 0.5, "fundamental": 0.5}, 1.0),
        outcome("MSFT", 5, {"technical": -0.5, "fundamental": 0.5}, -1.0),
        outcome("VZ", 20, {"technical": -0.5}, 2.0),
        outcome("VZ", 5, {"forecast:a:old": 0.9}, 1.0),
        # Outcomes scored against another target never train v2 weights.
        {**outcome("VZ", 5, {"fundamental": 1.0}, 1.0), "target": None},
    ]
    w = shadow.candidate_weights(
        history, {"technical": 0.3, "fundamental": 0.5, "forecast:a:new": 0.4}, "VZ", 5
    )
    assert w["weights"]["technical"] > w["weights"]["fundamental"] == 0
    assert w["counts"]["technical"] == {"stock": 0, "pooled": 2}
    assert w["counts"]["forecast:a:new"]["pooled"] == 0
    assert w["method"] == "family-budget-shrunk-information-coefficient.v2"


def test_skilled_confident_signal_outweighs_timid_noise():
    # v1 squared error gave 0.459 to a 56%-hit signal and 0.541 to zero-skill noise.
    import random

    rng = random.Random(7)
    history = []
    for _ in range(2000):
        z = rng.gauss(0, 1)
        target = 1 if z > 0 else -1
        bold = 0.6 * (target if rng.random() < 0.56 else -target)
        history.append(outcome("X", 5, {"bold_skilled": bold, "timid_noise": rng.uniform(-0.1, 0.1)}, z))
    w = shadow.candidate_weights(history, {"bold_skilled": 0, "timid_noise": 0}, "X", 5)
    assert w["weights"]["bold_skilled"] > 0.8
    assert w["skill"]["bold_skilled"] > 0.05 > abs(w["skill"]["timid_noise"])


def test_no_positive_skill_abstains_instead_of_forcing_weights():
    history = [outcome("VZ", 5, {"technical": v, "fundamental": v}, -v) for v in (0.5, -0.5, 0.8)]
    w = shadow.candidate_weights(history, {"technical": 0.3, "fundamental": 0.5}, "VZ", 5)
    assert w["status"] == "NO_POSITIVE_SKILL"
    assert set(w["weights"].values()) == {0.0}


def test_regime_is_a_market_signal_and_not_in_the_stock_candidate():
    w = shadow.candidate_weights([], {"regime": 0.8, "technical": 0.3}, "VZ", 5)
    assert w["weights"] == {"regime": 0.0, "technical": 1.0}
    assert w["market_components_excluded"] == ["regime"]


def test_market_move_is_not_stock_skill():
    # Stock rises exactly with the market: raw direction "hit", no stock-specific move.
    s = snapshot(beta=1.0, components={"technical": 0.5, "regime": 0.6}, agent_action="LONG_STOCK")
    result = score(s, path(s, 110), market=path(s, 110))
    assert result["residual_return_pct"] == pytest.approx(0)
    assert result["candidate_hit"] is None and result["baseline_hit"] is None
    assert result["agent_action_hit"] is True          # the agent holds the raw stock
    assert result["market_component_hits"] == {"regime": True}
    # The forecast predicts the raw price, so its own metrics stay raw.
    assert result["forecast_scores"]["forecast:lstm:v1"]["brier"] == pytest.approx(0.09)


def test_beta_is_frozen_clipped_and_defaults_when_missing():
    s = snapshot(beta=5.0, ex_ante_vol=0.3)
    up = score(s, path(s, 110), market=path(s, 102))
    assert up["beta"] == 3.0 and up["beta_basis"] == "frozen_beta_63_vs_spy"
    assert up["residual_return_pct"] == pytest.approx(10 - 3 * 2)
    assert up["residual_z"] == pytest.approx(0.04 / (0.3 * (5 / 252) ** 0.5))
    default = score(snapshot(), path(s, 110), market=path(s, 102))
    assert default["beta"] == 1.0 and default["beta_basis"] == "default_beta"
    assert default["component_values"] == {"technical": 0.5}


def test_snapshot_capture_blocks_stale_origin_before_any_db_read():
    decision = {"ticker": "VZ", "decision_id": "d", "price_date": "2026-08-31"}
    assert (
        shadow.capture(None, decision, {}, {"state": {}}, now="2026-09-01T21:00:00Z")[
            "status"
        ]
        == "WITHHELD"
    )


def test_naive_capture_timestamp_is_rejected():
    with pytest.raises(ValueError, match="NOT_AWARE"):
        shadow.capture(None, {}, {}, {}, now=datetime(2026, 9, 1))


def test_flat_days_keep_price_metrics_without_fabricating_direction_accuracy():
    metrics = [
        {"brier": None, "median_abs_return_error_pct": 5, "interval_80_covered": True}
    ]
    assert shadow.metric_mean(metrics, "brier") is None
    assert shadow.metric_mean(metrics, "median_abs_return_error_pct") == 5
    assert shadow.metric_mean(metrics, "interval_80_covered") == 1


@pytest.mark.parametrize("version,forecast_count", [("v1", 1), (None, 0)])
def test_capture_freezes_exchange_timestamp_and_requires_forecast_version(
    monkeypatch, version, forecast_count
):
    saved = {}
    monkeypatch.setattr(
        shadow.db, "fetch_company", lambda *a: {"sector": "communications"}
    )
    monkeypatch.setattr(shadow, "training_history", lambda *a: [])
    monkeypatch.setattr(shadow.research_store, "get", lambda *a: None)

    def save(conn, kind, key, payload, ticker):
        saved[(kind, key)] = payload

    monkeypatch.setattr(shadow.research_store, "save", save)
    decision = {
        "ticker": "VZ",
        "decision_id": "d",
        "price_date": "2026-09-01",
        "input_sha256": "a" * 64,
    }
    packet = {
        "model_distribution": {
            "as_of": "2026-09-01",
            "generated_at": "2026-09-01T20:05:00Z",
            "forecast_id": "f",
            "model_version": version,
            "short_horizon_models": {
                "lstm": {"horizons": {"5d": {"days": 5, "prob_positive": 0.7}}}
            },
        }
    }
    result = shadow.capture(
        None,
        decision,
        packet,
        {"state": {"signal_components": {"technical": 0.5}, "bias_score": 0.5,
                   "technical": {"features": {"beta_63_vs_spy": 1.3, "realized_vol_20": 0.25}}}},
        now="2026-09-01T21:00:00Z",
    )
    assert result["status"] == "CAPTURED"
    snap = saved[(shadow.SNAPSHOT, "d:5")]
    assert snap["first_future_session"] == "2026-09-02"
    assert len(snap["forecasts"]) == forecast_count
    assert (snap["target"], snap["beta"], snap["ex_ante_vol"]) == (shadow.TARGET, 1.3, 0.25)
    assert saved[(shadow.WEIGHTS, "d:5")]["training_history_hash"]
