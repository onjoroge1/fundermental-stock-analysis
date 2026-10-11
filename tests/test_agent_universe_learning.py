"""Adversarial checks for universe expansion and prospective paper learning."""

from datetime import datetime, timezone
import pytest
from stock_machine.agents.contracts import AGENT_UNIVERSE, PILOT, Policy, Decision
from stock_machine.market_calendar import next_close_after
from stock_machine.agent_intelligence import bandit, outcomes, reward
from stock_machine.agent_performance import summarize


def run(action="LONG_STOCK", **overrides):
    return {
        "ticker": "HIMS",
        "decision_id": "decision",
        "mode": "PAPER",
        "learning_contract": outcomes.CONTRACT,
        "learning": {
            "contract": outcomes.CONTRACT,
            "execution_session": "2026-09-01",
            "due_session": "2026-09-30",
        },
        "state": {"paper_eligible": True},
        "bandit": {"selected": {"action": action}},
        **overrides,
    }


def test_new_policy_covers_existing_54_and_preserves_legacy_pilot():
    assert len(AGENT_UNIVERSE) == 54 and set(PILOT) < set(AGENT_UNIVERSE)
    assert Policy().tickers == AGENT_UNIVERSE
    assert Policy(policy_id="research-pilot-v1", tickers=PILOT).tickers == PILOT
    with pytest.raises(ValueError):
        Policy(tickers=(*AGENT_UNIVERSE, "UNKNOWN"))


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-10-07T21:00:00Z", "2026-10-08"),
        ("2026-10-07T19:00:00Z", "2026-10-07"),
        ("2026-10-07T20:00:00Z", "2026-10-08"),
        ("2026-09-04T21:00:00Z", "2026-09-08"),
        ("2026-11-27T18:00:00Z", "2026-11-30"),
    ],
)
def test_prospective_fill_uses_actual_exchange_close_holidays_and_early_close(
    stamp, expected
):
    assert next_close_after(stamp) == expected


def test_naive_decision_timestamp_cannot_authorize_a_fill():
    with pytest.raises(ValueError):
        next_close_after("2026-10-07T21:00:00")


@pytest.mark.parametrize(
    "value,expected",
    [
        (run(learning_contract="old"), "EXCLUDED_LEGACY_HYPOTHETICAL"),
        (run(learning_contract="executed-paper.v3"), "EXCLUDED_PRIOR_LEARNING_CONTRACT"),
        (run(state={"paper_eligible": False}), "EXCLUDED_BLOCKED_STATE"),
        (run(learning={}), "EXCLUDED_INVALID_LEARNING_WINDOW"),
        (run(learning={"contract": outcomes.CONTRACT, "execution_session": "2026-10-01",
                       "due_session": "2026-09-30"}), "EXCLUDED_INVALID_LEARNING_WINDOW"),
    ],
)
def test_invalid_and_legacy_runs_are_not_training_samples(value, expected):
    assert outcomes.learning_status(value, "2026-10-07")[0] == expected


@pytest.mark.parametrize("action", ["LONG_STOCK", "SHORT_STOCK", "NO_TRADE"])
def test_every_eligible_decision_matures_on_its_frozen_window(action):
    # Shadow, abstaining, held or capacity-blocked decisions all have an
    # observable outcome; maturity depends only on the frozen window.
    for mode in ("PAPER", "SHADOW"):
        value = run(action, mode=mode)
        assert outcomes.learning_status(value, "2026-09-29") == ("PENDING_MATURITY", "2026-09-30")
        assert outcomes.learning_status(value, "2026-09-30") == ("READY_COUNTERFACTUAL", "2026-09-30")


def test_learning_window_uses_the_journal_time_like_a_paper_fill():
    from uuid import uuid4

    class Conn:
        def __init__(self, value):
            self.value = value
        def execute(self, *args):
            return self
        def fetchone(self):
            return self.value
    decision = str(uuid4())
    window = outcomes.learning_window(Conn(("2026-10-07 21:00:00+00",)), decision, "2026-10-07T19:00:00+00:00")
    assert window["entry_basis"] == "journal_recorded_at"
    assert window["execution_session"] == next_close_after("2026-10-07T21:00:00Z") == "2026-10-08"
    assert window["due_session"] == "2026-11-05"
    assert window["overlap_weight"] == pytest.approx(1 / 20)
    fallback = outcomes.learning_window(Conn(None), "not-a-uuid", "2026-10-07T19:00:00+00:00")
    assert fallback["entry_basis"] == "observed_at" and fallback["execution_session"] == "2026-10-07"


def test_learning_and_paper_horizons_agree():
    from stock_machine import agent_trading
    assert outcomes.HORIZON_SESSIONS == agent_trading.HOLDING_SESSIONS


def test_missing_path_session_and_unadjusted_fallback_do_not_earn_rewards(monkeypatch):
    rows = [
        {"date": "2026-09-01", "adj_close": 100.0, "close": 100.0},
        {"date": "2026-09-03", "adj_close": 110.0, "close": 110.0},
    ]
    monkeypatch.setattr(outcomes.db, "fetch_prices", lambda *args: rows)
    with pytest.raises(ValueError, match="PATH_SESSION_MISSING"):
        outcomes._stock_outcome(None, "HIMS", "LONG_STOCK", "2026-09-01", "2026-09-03")
    rows.insert(1, {"date": "2026-09-02", "adj_close": None, "close": 105.0})
    with pytest.raises(ValueError, match="ADJUSTED_PRICE_INVALID"):
        outcomes._stock_outcome(None, "HIMS", "LONG_STOCK", "2026-09-01", "2026-09-03")


@pytest.mark.parametrize(
    "field,value",
    [
        ("gross_return_pct", float("nan")),
        ("max_drawdown_pct", 1),
        ("costs_pct", -1),
        ("capital_used_pct", 101),
        ("turnover_pct", True),
        ("risk_scale_pct", 0.0),
    ],
)
def test_reward_rejects_invalid_operating_inputs(field, value):
    args = dict(
        gross_return_pct=1.0,
        max_drawdown_pct=-1.0,
        capital_used_pct=1.0,
        turnover_pct=2.0,
        costs_pct=0.2,
        risk_scale_pct=9.0,
    )
    args[field] = value
    with pytest.raises(ValueError, match="REWARD_INPUT_INVALID"):
        reward.compute(**args)


def test_cold_start_does_not_trade_on_no_evidence():
    # Every decision earns counterfactual labels for both directions, so the
    # model learns without exploring; it trades once it is confident.
    state = {"bias_score": 0.5, "signal_components": {"technical": 0.4}}
    picks = {
        bandit.select(state, ["LONG_STOCK", "NO_TRADE"], decision_key=str(i))["selected"]["action"]
        for i in range(50)
    }
    assert picks == {"NO_TRADE"}

def paper_position(**overrides):
    return {
        "position_id": "p1",
        "ticker": "HIMS",
        "side": "LONG",
        "status": "OPEN",
        "entry_market_date": "2026-09-30",
        "exit_market_date": None,
        "entry_notional_usd": 1000.0,
        "entry_cost_usd": 1.0,
        "realized_pnl_usd": None,
        **overrides,
    }


def test_weekly_report_is_account_weighted_and_keeps_costs_and_short_direction():
    days = [
        "2026-09-30",
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
    ]
    prices = {
        "HIMS": [
            {"date": d, "adj_close": p}
            for d, p in zip(days, [100, 105, 99, 100, 104, 110])
        ],
        "SPY": [
            {"date": d, "adj_close": p}
            for d, p in zip(days, [100, 100, 100, 100, 100, 101])
        ],
    }
    value = summarize(
        [paper_position(side="SHORT")], prices, start=days[0], end=days[-1]
    )
    assert value["status"] == "OK" and value["period_sessions"] == 5
    assert value["account_pnl_usd"] == -100 and value[
        "account_return_pct"
    ] == pytest.approx(-0.1000, abs=0.0001)
    assert value["spy_return_pct"] == 1 and value["excess_vs_spy_pct"] == pytest.approx(
        -1.1000, abs=0.0001
    )
    assert value["positions"][0]["period_pnl_usd"] == -100


def test_weekly_report_withholds_aggregate_if_any_held_session_is_missing():
    prices = {
        "HIMS": [
            {"date": "2026-09-30", "adj_close": 100},
            {"date": "2026-10-07", "adj_close": 110},
        ],
        "SPY": [
            {"date": "2026-09-30", "adj_close": 100},
            {"date": "2026-10-07", "adj_close": 101},
        ],
    }
    value = summarize([paper_position()], prices, start="2026-09-30", end="2026-10-07")
    assert value["status"] == "ATTENTION" and value["account_return_pct"] is None
    assert value["daily_equity"] == [] and value["missing_inputs"]


def test_closed_paper_cash_is_not_rewritten_by_current_prices():
    prices = {
        "SPY": [
            {"date": "2026-09-30", "adj_close": 100},
            {"date": "2026-10-07", "adj_close": 101},
        ]
    }
    value = summarize(
        [
            paper_position(
                status="CLOSED",
                entry_market_date="2026-09-29",
                exit_market_date="2026-09-30",
                realized_pnl_usd=-20,
            )
        ],
        prices,
        start="2026-09-30",
        end="2026-10-07",
    )
    assert value["status"] == "OK" and value["account_pnl_usd"] == 0
