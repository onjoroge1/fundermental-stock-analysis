"""Adversarial regressions from the September 2026 audit, using synthetic data."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from stock_machine import db, market_calendar, prediction, webapp
from stock_machine.alpha_forecast import expectation_features
from stock_machine.backtest import meta_model, regime_model
from stock_machine.backtest.engine import TickerData, target_session
from stock_machine.backtest.panel_expectations import expectations_as_of
from stock_machine.data_quality import assess_dataset, readiness_for_snapshots
from stock_machine.expectations import consensus_revision, known_surprises
from stock_machine.normalization.financial_periods import build_periods
from stock_machine.portfolio.constructor import build_proposal


def test_late_balance_sheet_fact_cannot_enter_earlier_information_set():
    flow = {"val": 100, "start": "2025-01-01", "end": "2025-03-31",
            "filed": "2025-05-01", "form": "10-Q", "accn": "early", "fy": 2025, "fp": "Q1"}
    instant = {"val": 1000, "end": "2025-03-31", "filed": "2025-08-01",
               "form": "10-Q", "accn": "late", "fy": 2025, "fp": "Q2"}
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [flow]}},
                                     "Assets": {"units": {"USD": [instant]}}}}}
    quarter = build_periods(facts)[0][0]
    assert quarter["fields"]["total_assets"] == 1000
    assert quarter["available_at"] == "2025-08-02"


def test_later_high_priority_tag_cannot_replace_first_reported_value():
    early = {"val": 100, "start": "2025-01-01", "end": "2025-03-31",
             "filed": "2025-05-01", "form": "10-Q", "accn": "early", "fy": 2025, "fp": "Q1"}
    late = {**early, "val": 150, "filed": "2025-08-01", "accn": "late"}
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [early]}},
        "RevenuesNetOfInterestExpense": {"units": {"USD": [late]}},
    }}}
    quarter = build_periods(facts)[0][0]
    assert quarter["fields"]["revenue"] == 100
    assert quarter["available_at"] == "2025-05-02"


def test_fiscal_period_roll_is_missing_revision_in_both_model_lanes():
    rows = [
        {"snapshot_date": "2025-04-01", "period_type": "quarter",
         "forecast_period_end": "2025-06-30", "eps_mean": 2, "revenue_mean": 100},
        {"snapshot_date": "2025-04-02", "period_type": "quarter",
         "forecast_period_end": "2025-09-30", "eps_mean": 4, "revenue_mean": 200},
    ]
    assert expectations_as_of(rows, [], "2025-04-03")["eps_revision_pct"] is None
    assert expectation_features("2025-04-03", rows, [])[0] == 0.0


def test_revision_window_is_elapsed_time_independent_of_fetch_frequency():
    base = {"period_type": "quarter", "forecast_period_end": "2025-06-30", "source": "vendor"}
    sparse = [{**base, "snapshot_date": "2025-01-01", "eps_mean": 1.0},
              {**base, "snapshot_date": "2025-02-01", "eps_mean": 1.2}]
    dense = [*sparse, *[{**base, "snapshot_date": f"2025-01-{d:02d}", "eps_mean": 1.1}
                       for d in range(20, 31)]]
    assert consensus_revision(sparse, "2025-02-15")["eps_revision_pct"] == pytest.approx(20)
    assert consensus_revision(dense, "2025-02-15") == consensus_revision(sparse, "2025-02-15")


def test_late_observed_surprise_is_not_backdated_to_earnings_date():
    rows = [{"date": "2025-01-20", "available_at": "2025-03-01T13:00:00Z", "surprise_pct": 90}]
    assert known_surprises(rows, "2025-02-15") == []
    assert len(known_surprises(rows, "2025-03-02")) == 1


@pytest.mark.parametrize("timestamp,expected", [
    ("2026-09-07T21:00:00+00:00", "2026-09-04"),  # Labor Day
    ("2026-11-27T17:59:00+00:00", "2026-11-25"),  # before early close
    ("2026-11-27T18:00:00+00:00", "2026-11-27"),
    ("2025-01-09T22:00:00+00:00", "2025-01-08"),  # exceptional closure
])
def test_health_uses_exchange_holidays_and_actual_close(timestamp, expected):
    assert market_calendar.latest_completed_session(datetime.fromisoformat(timestamp)) == expected


def test_recent_retrieval_cannot_make_old_prices_trade_eligible():
    today = date(2026, 9, 5)
    stale = assess_dataset("prices", [{"date": "2025-01-02", "close": 100, "volume": 10}], as_of=today)
    snapshots = {d: {"status": "PASS", "observed_at": today.isoformat(), "reasons": []}
                 for d in ("fundamentals", "prices", "filings")}
    snapshots["prices"] = {**stale, "observed_at": today.isoformat(), "last_checked_at": today.isoformat()}
    result = readiness_for_snapshots(snapshots, as_of=today)
    assert result["status"] == "BLOCKED"
    assert result["trade_eligible"] is False


def test_stale_forecast_cannot_be_served_ok_when_stored_prices_are_also_stale(monkeypatch):
    monkeypatch.setattr(market_calendar, "market_now", lambda: datetime(2026, 9, 5, tzinfo=timezone.utc))
    monkeypatch.setattr(db, "connect", MagicMock())
    monkeypatch.setattr(db, "fetch_prices", lambda *a: [{"date": "2025-01-02"}])
    monkeypatch.setattr(db, "latest_prediction_forecast", lambda *a: {
        "status": "OK", "ticker": "TEST", "as_of": "2025-01-02", "model_version": prediction.MODEL_VERSION})
    assert webapp.predict("TEST")["status"] == "STALE"


def test_workers_preserve_alpha_and_complete_input_identity(monkeypatch):
    from stock_machine import forecast_service as service, control_plane
    from scripts import predict_all
    rows = [{"date": "2026-09-04", "adj_close": 100.0}]
    monkeypatch.setattr(service, "latest_completed_session", lambda: "2026-09-04")
    monkeypatch.setattr(db, "connect", MagicMock())
    monkeypatch.setattr(db, "fetch_prices", lambda *a: rows)
    monkeypatch.setattr(db, "latest_dataset_snapshots", lambda *a: [])
    monkeypatch.setattr(db, "list_companies", lambda *a: [{"ticker": "TEST"}])
    monkeypatch.setattr(service, "fetch_consensus_history", lambda *a: [])
    monkeypatch.setattr(service, "fetch_surprise_history", lambda *a: [])
    monkeypatch.setattr(service, "forecast", lambda *a: {
        "status": "OK", "ticker": "TEST", "as_of": "2026-09-04", "model_version": prediction.MODEL_VERSION,
        "primary_model": "bootstrap", "validation": {"verdict": {"lstm_beats_baseline": False}},
        "models": {"bootstrap": {"horizons": {"12m": {"p50": 100, "prob_positive": 0.5}}}}})
    monkeypatch.setattr(service, "forecast_alpha", lambda *a, **k: {"status": "OK", "horizons": {"63": {"status": "OK"}}})
    saved = []
    monkeypatch.setattr(db, "save_prediction_forecast", lambda conn, result: saved.append(deepcopy(result)))
    assert control_plane._forecast_one("TEST")["alpha_status"] == "OK"
    assert predict_all.main() == 0
    assert len(saved) == 2
    assert saved[0] == saved[1]
    assert saved[0]["alpha_forecast"]["horizons"]["63"]["status"] == "OK"
    assert {"prices", "benchmark_prices", "consensus", "earnings_surprises"} <= saved[0]["input_data_versions"].keys()


def test_unmatured_outcomes_cannot_change_earlier_ensemble_weights(monkeypatch):
    dates = [date(2010+i//4, 1+(i%4)*3, 1).isoformat() for i in range(32)]
    panel = [{"as_of": d, "ticker": f"T{i:02}", "components": {"growth": float(i)},
              "factors": {}, "expectations": {}, "forward": {"fwd_12m_pct": float(i)}}
             for d in dates for i in range(12)]
    class OppositeModel:
        def fit(self, x, y):
            return self
        def predict(self, x):
            return [-0.5*r[0] for r in x]
    monkeypatch.setattr(meta_model.nonlinear_model, "_new_model", OppositeModel)
    monkeypatch.setattr(meta_model, "ridge_fit", lambda rows, alpha: [1.0]+[0.0]*(len(rows[0][0])-1))
    altered = deepcopy(panel)
    for row in altered:
        if row["as_of"] in {"2013-04-01", "2013-07-01", "2013-10-01"}:
            row["forward"]["fwd_12m_pct"] *= -1
    def weights(rows):
        return next(r["weights"] for r in meta_model.walk_forward(rows)["per_date"] if r["as_of"] == "2014-01-01")
    assert weights(panel) == weights(altered)


def test_regime_state_and_its_magnitude_survive_feature_construction():
    from stock_machine.regime import sector_etf
    from stock_machine.sectors import SIC_RANGES
    def features(state):
        rows = [{"as_of": "2025-01-01", "ticker": f"T{i}", "components": {},
                 "factors": {"momentum_12m_pct": i}, "expectations": {},
                 "regime": {"features": {"market_mom_63": state}}} for i in range(8)]
        return regime_model._zscore_by_date(rows)[("2025-01-01", "T7")]
    raw = regime_model.FEATURE_NAMES.index("regime.market_mom_63")
    interaction = regime_model.FEATURE_NAMES.index("regime.market_mom_63_x_momentum_12m_pct")
    assert features(-0.25)[raw] == -0.25
    assert features(-0.50)[interaction] == pytest.approx(2*features(-0.25)[interaction])
    assert all(sector_etf(label) for _, _, label in SIC_RANGES)


def test_failed_stale_alpha_cannot_produce_position():
    rows = [{"date": (date(2024, 1, 1)+timedelta(days=i)).isoformat(),
             "adj_close": 100+i*0.1+(i%7)*0.02} for i in range(320)]
    failed = {"as_of": "2020-01-01", "alpha_forecast": {"status": "OK", "horizons": {
        "63": {"status": "OK", "expected_excess_return_pct": 8, "prob_outperform": 0.7,
               "validation": {"passes": False}}}}}
    result = build_proposal([{"ticker": "TEST", "sector": "Tech", "forecast": failed,
                              "price_rows": rows, "data_quality": {"status": "BLOCKED"}}], rows)
    assert result["positions"] == []
    assert result["rejected"][0]["readiness"]["eligible"] is False


def test_outcome_lookup_requires_exact_completed_target(monkeypatch):
    import stock_machine.backtest.engine as engine
    monkeypatch.setattr(engine, "latest_completed_session", lambda *a: "2026-09-04")
    td = TickerData.__new__(TickerData)
    td.prices = [{"date": "2026-09-03", "adj_close": 110, "close": 110}]
    td._dates = ["2026-09-03"]
    assert td._outcome_price("2026-09-10") is None
    assert td._outcome_price("2026-09-04") is None
    assert td._outcome_price("2026-09-03") == 110
    assert target_session("2026-06-01", 3) == "2026-09-01"


def test_paper_endpoints_share_one_adjustment_vintage_and_one_read(monkeypatch):
    from stock_machine.forward_paper_v2 import build_mark
    cohort = {"cohort_id": "fixture", "contract": {
        "mode": "long_only", "longs": ["A", "B"], "shorts": [],
        "frozen_eligible_universe": ["A", "B"], "entry_market_date": "2025-01-02",
        "entry_adjusted_close": {"A": 100, "B": 100}, "cost_bps": 0}}
    fetch = MagicMock(return_value=[{"date": "2025-01-02", "close": 100, "adj_close": 99},
                                   {"date": "2025-02-03", "close": 99, "adj_close": 99}])
    monkeypatch.setattr(db, "fetch_prices", fetch)
    mark = build_mark(MagicMock(), cohort, "2025-02-03")
    assert mark["gross_return_pct"] == 0
    assert mark["entry_adjustment_changed"] is True
    assert fetch.call_count == 2


def test_strategy_selection_does_not_consult_evaluation_outcomes():
    from tests.test_strategy_lab_v2 import _panel
    from stock_machine.strategy_lab_v2 import run
    panel = _panel()
    first = run(panel)
    changed = deepcopy(panel)
    for row in changed:
        if row["as_of"] >= first["date_split"]["evaluation_start"]:
            row["forward"]["fwd_3m_pct"] *= -20
    second = run(changed)
    for mode in first["modes"]:
        assert first["modes"][mode]["selected_candidate"] == second["modes"][mode]["selected_candidate"]
        assert first["modes"][mode]["best_single_factor"] == second["modes"][mode]["best_single_factor"]
        assert sum(s["promotion"]["status"] != "NOT_SELECTED_ON_DEVELOPMENT"
                   for s in first["modes"][mode]["strategies"].values() if s["kind"] != "baseline") == 1


def test_insufficient_or_constant_score_samples_cannot_claim_confidence():
    from stock_machine.backtest.statistics import mean_uncertainty
    assert mean_uncertainty([1.0, 2.0])["status"] == "INSUFFICIENT_DATA"
    assert mean_uncertainty([1.0]*40, lags=4)["lower"] is None
    assert mean_uncertainty([1.0+i%4 for i in range(40)], lags=4)["lower"] > 0


def test_alpha_scoring_does_not_extend_horizon_across_missing_bars(monkeypatch):
    from stock_machine import alpha_calibration
    monkeypatch.setattr(alpha_calibration, "latest_completed_session", lambda: "2026-09-14")
    aligned = [("2026-09-04", 100, 100), ("2026-09-09", 102, 101)]
    # The next session was Sept 8, missing from this vendor series.
    assert alpha_calibration._realized(aligned, "2026-09-04", 1) is None


def test_option_dte_converts_calendar_days_to_exchange_sessions():
    # Friday to Tuesday across Labor Day contains one trading session.
    assert market_calendar.calendar_dte_to_sessions(date(2026, 9, 4), 4) == 1


def test_macro_revision_cannot_rewrite_earlier_information_set():
    from stock_machine.macro import features_as_of
    early = {"observation_date": "2026-01-01", "available_at": "2026-01-02", "value": 15}
    revised = {**early, "available_at": "2026-02-01", "value": 40}
    assert features_as_of({"VIXCLS": [early, revised]}, "2026-01-15")["features"]["vix_level"] == 15
    assert features_as_of({"VIXCLS": [early, revised]}, "2026-02-02")["features"]["vix_level"] == 40


def test_baseline_comparison_uses_identical_names_when_a_factor_is_missing():
    from stock_machine.backtest.comparisons import baseline_scores
    rows = [{"factors": {"revenue_yoy_pct": i if i < 10 else None}} for i in range(11)]
    predictions = [*range(10), -1000]
    result = baseline_scores(rows, predictions, list(range(11)))["revenue_yoy"]
    assert result == {"n": 10, "model_ic": 1.0, "baseline_ic": 1.0}


def test_challenger_comparison_rejects_different_name_sets():
    from stock_machine.backtest.comparisons import evidence
    rows = [{"as_of": f"2000-{i:03}", "tickers": ["A", "B"], "ic": 0.3+(i%3)*0.02} for i in range(40)]
    other = [{**r, "ic": 0.0} for r in rows]
    matched = evidence(rows, "ic", "fwd_12m_pct", {"control": other}, include_baselines=False)
    assert matched["passes"] is True
    mismatched = [{**r, "tickers": ["A", "C"]} for r in other]
    failed = evidence(rows, "ic", "fwd_12m_pct", {"control": mismatched}, include_baselines=False)
    assert failed["comparisons"]["control"]["n"] == 0
    assert failed["passes"] is False


def test_historical_valuation_price_uses_the_asof_share_basis():
    from stock_machine.bundle import _price_lookup_on_share_basis
    prices = [{"date": "2020-01-02", "close": 25, "adj_close": 20}]
    splits = [{"date": "2022-01-03", "action_type": "split", "value": 4}]
    assert _price_lookup_on_share_basis(prices, splits, "2020-02-01")("2020-01-02") == 100
    assert _price_lookup_on_share_basis(prices, splits, "2023-02-01")("2020-01-02") == 25
