"""Fair-value convergence replay: solid companies traded toward a P/E fair value."""

import pytest

from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import fair_value_replay as fv
from tests.test_direction_replay import SPY, series


def test_protocol_is_hashed_and_family_wise():
    assert fv.PROTOCOL_SHA256 == digest(fv.PROTOCOL)
    assert fv.PROTOCOL["base_sha256"] == dr.PROTOCOL_SHA256
    assert len(fv.PROTOCOL["hypotheses"]) == 5 and fv.Z_FAMILY == pytest.approx(2.5758, abs=1e-4)


def test_own_history_gap_uses_only_prior_months_and_needs_36():
    history = [20.0] * 36
    assert fv.own_history_gap(history, 16.0) == pytest.approx(0.25)    # cheap vs its own history
    assert fv.own_history_gap(history, 25.0) == pytest.approx(-0.20)   # rich
    assert fv.own_history_gap(history[:35], 16.0) is None
    assert fv.own_history_gap(history, -5.0) is None                   # losses: no P/E fair value
    long_history = [10.0] * 30 + [20.0] * 60
    assert fv.own_history_gap(long_history, 20.0) == 0.0               # only the last 60 months


def test_sector_gap_falls_back_to_the_universe_median():
    obs = {f"T{i}": ("Tech", 10.0 + i) for i in range(4)}
    obs.update({f"U{i}": ("Other", 30.0) for i in range(5)})
    obs["SOLO"] = ("Lonely", 20.0)
    obs["LOSS"] = ("Tech", -3.0)
    gaps = fv.sector_gaps(obs)
    assert gaps["T0"] == pytest.approx(11.5 / 10.0 - 1)                # Tech median of 10..13
    assert gaps["SOLO"] == pytest.approx(25.0 / 20.0 - 1)              # universe median of 10 names
    assert "LOSS" not in gaps


def test_solid_company_filter():
    good = {"pe_ttm": 15.0, "profitability_score": 60, "financial_health_score": 55}
    assert fv.solid(good)
    assert not fv.solid({**good, "financial_health_score": 40})
    assert not fv.solid({**good, "pe_ttm": -2.0})
    assert not fv.solid(None)


def universe():
    out = {}
    for i in range(10):
        drift = (i - 5) * 0.0006
        prices = series(drift, start=40 + 5 * i)
        by_date = {r["date"]: r["adj_close"] for r in prices}

        def observe(as_of, b=by_date, k=i):
            # Earnings flat, so P/E moves with price: a falling stock looks cheap vs its history.
            price = b.get(as_of)
            pe = None if price is None else price / (2.0 + 0.1 * k)
            return {"composite": 70, "detail": {"pe_ttm": pe, "profitability_score": 60,
                                                "financial_health_score": 60}}
        out[f"T{i}"] = {"prices": prices, "sector_rows": None, "sector": "Tech" if i < 5 else "Other",
                        "observe": observe}
    return out


def test_replay_trades_toward_fair_value_and_times_with_price_action():
    rows = fv.replay(universe(), spy=SPY, sessions=dr.month_end_sessions("2015-01-01", "2016-12-31"),
                     decision_start="2015-03-01")
    assert rows
    for r in rows:
        assert set(r["rewards"]) == {"60", "120"}
        if r["own_untimed_120"] != "FLAT":
            assert (r["own_untimed_120"] == "LONG") == (r["own_gap"] > 0)
        if r["own_timed_120"] != "FLAT":
            assert r["own_timed_120"] == r["own_untimed_120"]
            assert (r["technical_signal"] > 0) == (r["own_timed_120"] == "LONG")
    result = fv.evaluate(rows)
    assert result["hypotheses"]["H9"]["verdict"] == "INSUFFICIENT_COVERAGE"   # short fixture
    assert result["coverage"]["own_untimed_120"]["active_decisions"] >= result["coverage"]["own_timed_120"][
        "active_decisions"]


GOLDEN_FIXTURE_DIGEST = "54806e4b83a458baf640ee8ab2a8fe0c39d586355a290c8429331c39a7bf17d7"


def test_replay_output_is_unchanged_by_the_shared_decision_refactor():
    # Recorded before decision_row/policy_side were extracted; any change here
    # changes the replay the forward tracker is meant to confirm.
    rows = fv.replay(universe(), spy=SPY, sessions=dr.month_end_sessions("2015-01-01", "2016-12-31"),
                     decision_start="2015-03-01")
    assert digest(rows) == GOLDEN_FIXTURE_DIGEST


def test_single_session_decisions_match_the_replay():
    # The forward tracker decides one session at a time from observe_session;
    # its sector-rule side must equal the replay's for the same session.
    u = universe()
    session = "2016-03-31"
    replayed = {r["ticker"]: r for r in fv.replay(u, spy=SPY, sessions=dr.month_end_sessions("2015-01-01", "2016-03-31"),
                                                  decision_start=session)}
    assert len(replayed) >= 9  # enough names for the comparison to mean something
    observed = fv.observe_session(u, session)
    gaps = fv.sector_gaps({t: (sector, pe) for t, (_, _, sector, pe) in observed.items()})
    for ticker, (observation, detail, _, _) in observed.items():
        made = fv.decision_row(ticker, session, u[ticker], observation=observation, detail=detail, own_gap=None,
                               sector_gap=gaps.get(ticker), spy=SPY, regime=None)
        if ticker in replayed:
            assert made is not None
            assert made[0]["sector_timed_120"] == replayed[ticker]["sector_timed_120"]
            assert made[0]["sector_gap"] == replayed[ticker]["sector_gap"]
