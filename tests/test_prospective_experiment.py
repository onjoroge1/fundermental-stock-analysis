"""Synthetic future observations test timing, costs and experiment rejection."""
import copy
from datetime import datetime, timezone

from stock_machine.market_calendar import session_offset
from stock_machine.prospective_experiment import freeze, protocol, score, verdict

NOW = datetime(2026, 9, 16, 15, tzinfo=timezone.utc)


def fixture():
    config = protocol(["AAPL", "MSFT"])
    start = session_offset("2026-09-15", -63)
    history = {t: [{"date": start, "adj_close": 100}, {"date": "2026-09-15", "adj_close": v}]
               for t, v in [("AAPL", 130), ("MSFT", 110), ("SPY", 105)]}
    frozen = freeze(config, history, now=NOW)
    for t in history:
        for i in range(20):
            value = 100 + i * (1 if t == "AAPL" else .1)
            history[t].append({"date": session_offset(frozen["entry_date"], i), "open": 100,
                               "close": value, "adj_close": value})
    return config, frozen, history


def test_freeze_uses_completed_inputs_and_future_entry():
    config, frozen, history = fixture()
    assert frozen["as_of"] == "2026-09-15"
    assert frozen["entry_date"] == "2026-09-17"
    assert frozen["selected"] == ["AAPL"]
    assert not frozen["trade_execution"]
    # Future observations cannot alter the frozen ranking.
    history["MSFT"][-1]["adj_close"] = 999999
    assert freeze(config, history, now=NOW) == frozen


def test_future_results_are_pending_and_backdated_persistence_rejected():
    config, frozen, history = fixture()
    assert score(config, frozen, history, now=NOW, recorded_at=NOW.isoformat())["status"] == "PENDING_MATURITY"
    mature = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert score(config, frozen, history, now=mature)["status"] == "INVALID_PROSPECTIVE_TIMING"
    assert score(config, frozen, history, now=mature, recorded_at=mature.isoformat())["status"] == "INVALID_PROSPECTIVE_TIMING"


def test_realistic_costs_controls_and_missing_losers_block():
    config, frozen, history = fixture()
    mature = datetime(2026, 11, 1, tzinfo=timezone.utc)
    result = score(config, frozen, history, now=mature, recorded_at=NOW.isoformat())
    assert result["status"] == "SCORED"
    assert abs(result["challenger"]["net_return_pct"] - 18.5) < 1e-8
    assert abs(result["challenger"]["stress_net_return_pct"] - 18) < 1e-8
    assert result["excess_vs_equal_weight_pct"] > 0
    del history["MSFT"][-1]
    assert score(config, frozen, history, now=mature, recorded_at=NOW.isoformat())["status"] == "BLOCKED_MISSING_OUTCOMES"


def test_no_early_pass_and_explicit_drawdown_kill():
    config, frozen, history = fixture()
    result = score(config, frozen, history, now=datetime(2026, 11, 1, tzinfo=timezone.utc), recorded_at=NOW.isoformat())
    assert verdict(config, [result])["status"] == "PENDING_MATURITY"
    result["challenger"]["max_drawdown_pct"] = -21
    assert verdict(config, [result])["status"] == "REJECTED"


def test_overlap_and_failed_controls_cannot_pass():
    config, frozen, history = fixture()
    row = score(config, frozen, history, now=datetime(2026, 11, 1, tzinfo=timezone.utc), recorded_at=NOW.isoformat())
    assert verdict(config, [row] * 12)["status"] == "INVALID_OVERLAPPING_COHORTS"
    rows = []
    for i in range(12):
        r = copy.deepcopy(row)
        r["entry_date"] = session_offset(frozen["entry_date"], i * 21)
        r["exit_date"] = session_offset(r["entry_date"], 19)
        r["excess_vs_spy_pct"] = -1
        rows.append(r)
    assert verdict(config, rows)["status"] == "REJECTED"
