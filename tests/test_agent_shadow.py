from datetime import datetime, timezone
import pytest
from stock_machine.agent_intelligence import shadow
from stock_machine.market_calendar import session_offset, session_dates


def snapshot(**overrides):
    return {
        "ticker": "VZ",
        "decision_id": "d",
        "origin_session": "2026-09-01",
        # Scorable when the entry-aligned window (from 2026-09-02) matures.
        "due_session": session_offset("2026-09-02", 5),
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
    # Flat at 100 until the forecast target, then `last`: both the forecast
    # window (origin -> origin+h) and the entry window (entry -> entry+h) move.
    forecast_due = session_offset(s["origin_session"], s["horizon_sessions"])
    days = session_dates(s["origin_session"], s["due_session"])
    return [{"date": d, "adj_close": last if d >= forecast_due else 100} for d in days]


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
    market = flat_market(s)
    with pytest.raises(ValueError, match="MARKET_PATH_INCOMPLETE"):
        # SPY is needed over the entry window; drop its entry close.
        score(s, market=market[:1] + market[2:])


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
    from stock_machine.regime import sector_etf
    assert snap["sector_etf"] == sector_etf("communications") is not None
    assert saved[(shadow.WEIGHTS, "d:5")]["training_history_hash"]


def pooled_row(origin, horizon, z, candidate, baseline, ret=1.0, prob=None):
    row = {"origin_session": origin, "horizon_sessions": horizon, "residual_z": z,
           "candidate_score": candidate, "baseline_score": baseline,
           "candidate_hit": None if candidate == 0 else (candidate > 0) == (z > 0),
           "baseline_hit": None if baseline == 0 else (baseline > 0) == (z > 0),
           "realized_return_pct": ret, "forecast_scores": {}}
    if prob is not None:
        row["forecast_scores"] = {"m": {"brier": (prob - (ret > 0)) ** 2}}
    return row


def test_pooled_statistics_resample_blocks_and_report_skill_with_intervals():
    import random

    rng = random.Random(3)
    origins = session_dates("2026-10-01", "2027-03-31")
    rows = []
    for origin in origins:
        for _ in range(10):
            z = rng.gauss(0, 1)
            rows.append(pooled_row(origin, 5, z, 0.5 if z > 0 else -0.5, rng.choice([-0.5, 0.5])))
    value = shadow.pooled_statistics(rows)["5"]
    assert value["observations"] == len(rows) and value["blocks"] == -(-len(origins) // 5)
    assert value["point"]["candidate_ic"] > 0.7
    assert abs(value["point"]["baseline_ic"]) < 0.1
    low, high = value["interval_95"]["ic_difference"]
    assert 0 < low < value["point"]["ic_difference"] < high
    assert value["point"]["candidate_hit_rate"] == 1.0


def test_one_block_has_no_interval_however_many_stocks():
    rows = [pooled_row("2026-10-01", 20, z, z, z) for z in (1.0, -1.0) * 200]
    value = shadow.pooled_statistics(rows)["20"]
    assert value["blocks"] == 1 and value["interval_95"] == {}
    assert value["interval_basis"].startswith("fewer than two blocks")


def test_brier_skill_is_relative_to_the_base_rate():
    origins = session_dates("2026-10-01", "2026-12-31")
    flat = [pooled_row(o, 5, 1.0, 0, 0, ret=r, prob=0.5) for o in origins for r in (1.0, -1.0)]
    sharp = [pooled_row(o, 5, 1.0, 0, 0, ret=r, prob=0.9 if r > 0 else 0.1) for o in origins for r in (1.0, -1.0)]
    assert shadow.pooled_statistics(flat)["5"]["point"]["brier_skill:m"] == pytest.approx(0.0)
    assert shadow.pooled_statistics(sharp)["5"]["point"]["brier_skill:m"] == pytest.approx(1 - 0.01 / 0.25)


def test_outcome_records_frozen_scores_for_pooled_ic():
    s = snapshot()
    result = score(s)
    assert (result["candidate_score"], result["baseline_score"]) == (0.5, -0.5)


def protocol_rows(horizon, origins, candidate_skill, baseline_skill, seed=5, per=8):
    import random

    rng = random.Random(seed)
    rows = []
    for origin in origins:
        for _ in range(per):
            z = rng.gauss(0, 1)
            def score(skill):
                return (0.5 if z > 0 else -0.5) if rng.random() < skill else rng.choice([-0.5, 0.5])
            rows.append({**pooled_row(origin, horizon, z, score(candidate_skill), score(baseline_skill)),
                         "weights_protocol_sha256": shadow.WEIGHTS_PROTOCOL_SHA256})
    return rows


def test_weights_protocol_is_frozen_into_snapshots_and_outcomes():
    s = snapshot(weights_protocol_sha256=shadow.WEIGHTS_PROTOCOL_SHA256)
    assert score(s)["weights_protocol_sha256"] == shadow.WEIGHTS_PROTOCOL_SHA256
    assert shadow.WEIGHTS_PROTOCOL["primary_horizon_sessions"] == 20


def test_weights_test_needs_twenty_four_primary_blocks():
    origins = session_dates("2026-10-01", "2028-07-25")   # 23 blocks: still short of 24
    value = shadow.weights_promotion_test(protocol_rows(20, origins, 0.9, 0.0))
    assert value["status"] == "PENDING_EVIDENCE" and value["blocks"] == 23 < shadow.WEIGHTS_PROTOCOL["minimum_blocks"]
    assert value["promotion"] == "NOT_AUTHORIZED"


def test_better_candidate_passes_only_to_review_and_worse_does_not():
    origins = session_dates("2026-10-01", "2029-01-31")   # ~29 blocks of 20 sessions
    better = shadow.weights_promotion_test(protocol_rows(20, origins, 0.6, 0.0))
    assert better["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW"
    assert better["ic_difference_interval_95"][0] > 0 and better["trade_qualification"] is False
    assert 0 < better["detectable_difference_80pct"] < 1
    worse = shadow.weights_promotion_test(protocol_rows(20, origins, 0.0, 0.6))
    assert worse["status"] == "NOT_SUPERIOR"


def test_secondary_horizons_and_other_protocols_never_decide():
    origins = session_dates("2026-10-01", "2028-01-31")
    five_day = protocol_rows(5, origins, 0.9, 0.0)
    value = shadow.weights_promotion_test(five_day)
    assert value["status"] == "AWAITING_MATURED_OUTCOMES" and "5" in value["secondary"]
    stale = [{**r, "weights_protocol_sha256": "0" * 64} for r in protocol_rows(20, origins, 0.9, 0.0)]
    assert shadow.weights_promotion_test(stale)["status"] == "AWAITING_MATURED_OUTCOMES"
    changed = {**shadow.WEIGHTS_PROTOCOL, "minimum_blocks": 6}
    assert shadow.weights_promotion_test(protocol_rows(20, origins, 0.9, 0.0), changed)["status"] == "AWAITING_MATURED_OUTCOMES"


def test_signals_use_the_paper_entry_window_and_forecasts_their_origin_window():
    s = snapshot(entry_session="2026-09-02", agent_action="LONG_STOCK")
    days = session_dates("2026-09-01", s["due_session"])
    # The stock gaps up between the origin and entry closes, then is flat.
    rows = [{"date": d, "adj_close": 100 if d == "2026-09-01" else 110} for d in days]
    result = score(s, rows)
    assert result["entry_session"] == "2026-09-02"
    assert result["realized_return_pct"] == pytest.approx(0)        # nothing tradeable to capture
    assert result["forecast_return_pct"] == pytest.approx(10)       # but the forecast was right
    assert result["candidate_hit"] is None and result["agent_action_hit"] is None
    assert result["forecast_scores"]["forecast:lstm:v1"]["brier"] == pytest.approx(0.09)


def test_pre_alignment_snapshot_derives_entry_and_waits_for_it():
    legacy = snapshot(due_session=session_offset("2026-09-01", 5))   # origin-based, no entry fields
    origin, forecast_due, entry, due = shadow.windows(legacy)
    assert entry == "2026-09-02" and due == session_offset("2026-09-02", 5) > forecast_due
    with pytest.raises(ValueError, match="NOT_MATURE"):
        score(legacy, completed=forecast_due)


@pytest.mark.parametrize("learning,basis", [
    ({"execution_session": "2026-09-03"}, "paper_execution_session"),
    (None, "next_close_after_capture"),
])
def test_capture_freezes_the_paper_entry_session(monkeypatch, learning, basis):
    saved = {}
    monkeypatch.setattr(shadow.db, "fetch_company", lambda *a: {})
    monkeypatch.setattr(shadow, "training_history", lambda *a: [])
    monkeypatch.setattr(shadow.research_store, "get", lambda *a: None)
    monkeypatch.setattr(shadow.research_store, "save", lambda conn, kind, key, payload, ticker: saved.update({(kind, key): payload}))
    intelligence = {"state": {"signal_components": {"technical": 0.5}, "bias_score": 0.5}}
    if learning:
        intelligence["learning"] = learning
    shadow.capture(None, {"ticker": "VZ", "decision_id": "d", "price_date": "2026-09-01", "input_sha256": "a" * 64},
                   {}, intelligence, now="2026-09-01T21:00:00Z")
    snap = saved[(shadow.SNAPSHOT, "d:5")]
    expected = (learning or {}).get("execution_session", "2026-09-02")
    assert (snap["entry_session"], snap["entry_basis"]) == (expected, basis)
    assert snap["due_session"] == session_offset(expected, 5)
    assert snap["forecast_due_session"] == session_offset("2026-09-01", 5)


def test_sector_relative_return_is_a_diagnostic_beside_the_spy_target():
    s = snapshot(sector_etf="XLK", ex_ante_vol=0.3)
    days = session_dates(s["origin_session"], s["due_session"])
    sector = [{"date": d, "adj_close": 100 if d <= "2026-09-02" else 104} for d in days]
    result = shadow.evaluate(
        s, path(s), completed=s["due_session"], market_prices=flat_market(s), sector_prices=sector)
    assert result["sector_relative_return_pct"] == pytest.approx(10 - 4)
    assert result["sector_relative_basis"] == "XLK"
    assert result["sector_relative_z"] == pytest.approx(0.06 / (0.3 * (5 / 252) ** 0.5))
    assert result["residual_return_pct"] == pytest.approx(10)          # primary target unchanged
    missing = shadow.evaluate(s, path(s), completed=s["due_session"], market_prices=flat_market(s),
                              sector_prices=sector[2:])
    assert missing["sector_relative_z"] is None and missing["sector_relative_basis"] == "SECTOR_PATH_INCOMPLETE"
    assert score(snapshot())["sector_relative_basis"] == "UNAVAILABLE"


def test_pooled_statistics_report_skill_against_the_sector_too():
    origins = session_dates("2026-10-01", "2026-12-31")
    rows = []
    for o in origins:
        for z, zs in ((1.0, -1.0), (-1.0, 1.0)):
            rows.append({**pooled_row(o, 5, z, 0.5 if z > 0 else -0.5, 0.0), "sector_relative_z": zs})
    point = shadow.pooled_statistics(rows)["5"]["point"]
    # Perfect against SPY-relative, perfectly wrong against the sector: the
    # skill was sector rotation, not stock selection within the sector.
    assert point["candidate_ic"] == pytest.approx(1.0)
    assert point["candidate_ic_vs_sector"] == pytest.approx(-1.0)


def test_brier_reference_uses_only_outcomes_known_before_each_forecast():
    history_due = session_dates("2026-10-01", "2026-12-31")
    # 60 earlier outcomes, all up; the evaluated forecasts start after them.
    counts = {"5": {d: [1, 1] for d in history_due[:60]}}
    later = session_dates("2027-01-04", "2027-01-29")
    rows = [pooled_row(o, 5, 1.0, 0, 0, ret=r, prob=0.5) for o in later for r in (1.0, -1.0)]
    rates = shadow.prior_base_rates(rows, counts)
    assert set(rates.values()) == {(1.0, "PRIOR_252_SESSIONS")}
    point = shadow.pooled_statistics(rows, counts=counts)["5"]["point"]
    # A coin-flip forecast beats a "always up" prior when half the moves are down.
    assert point["brier_skill:m"] == pytest.approx(1 - 0.25 / 0.5)
    # Outcomes that complete on or after the forecast's origin are never used.
    future = {"5": {d: [0, 1] for d in session_dates("2027-01-04", "2027-06-30")}}
    assert set(shadow.prior_base_rates(rows, future).values()) == {(0.5, "DEFAULT_INSUFFICIENT_HISTORY")}


def test_too_little_prior_history_falls_back_to_one_half():
    counts = {"5": {d: [1, 1] for d in session_dates("2026-10-01", "2026-10-30")}}  # < 50 outcomes
    rows = [pooled_row("2027-01-04", 5, 1.0, 0, 0, ret=1.0, prob=0.6)]
    assert shadow.prior_base_rates(rows, counts)[id(rows[0])] == (0.5, "DEFAULT_INSUFFICIENT_HISTORY")
    value = shadow.pooled_statistics(rows, counts=counts)["5"]
    assert value["brier_default_reference_counts"] == {"m": 1}
