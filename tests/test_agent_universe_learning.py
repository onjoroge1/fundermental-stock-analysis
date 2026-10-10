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
        "learning_contract": "executed-paper.v3",
        "state": {"paper_eligible": True},
        "bandit": {"selected": {"action": action}},
        **overrides,
    }


def ledger(**overrides):
    return {
        "intent_status": "SIMULATED",
        "intent_action": "OPEN_LONG",
        "position_id": "pos",
        "position_status": "OPEN",
        "entry_market_date": "2026-09-01",
        "risk_snapshot": {"execution_contract": "prospective-next-close.v2"},
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
        (run(mode="SHADOW"), "EXCLUDED_SHADOW"),
        (run(state={"paper_eligible": False}), "EXCLUDED_BLOCKED_STATE"),
    ],
)
def test_invalid_and_legacy_runs_are_not_zero_reward_successes(value, expected):
    assert outcomes.learning_status(value, None, "2026-10-07")[0] == expected


def test_rejected_hold_and_close_intents_do_not_train_the_selected_stock_arm():
    for value, expected in [
        (ledger(intent_status="BLOCKED"), "EXCLUDED_REJECTED_INTENT"),
        (ledger(intent_action="HOLD", position_id=None), "EXCLUDED_NO_NEW_POSITION"),
        (ledger(intent_action="CLOSE", position_id=None), "EXCLUDED_NO_NEW_POSITION"),
    ]:
        assert outcomes.learning_status(run(), value, "2026-10-07")[0] == expected
    assert (
        outcomes.learning_status(run(), ledger(intent_status="PENDING"), "2026-10-07")[
            0
        ]
        == "PENDING_EXECUTION"
    )


def test_actual_entry_session_controls_maturity_and_only_closed_positions_are_ready():
    status, due = outcomes.learning_status(run(), ledger(), "2026-09-10")
    assert status == "PENDING_MATURITY" and due == "2026-09-30"
    assert outcomes.learning_status(run(), ledger(), "2026-10-07")[0] == "PENDING_EXIT"
    assert outcomes.learning_status(
        run(),
        ledger(position_status="CLOSED", exit_market_date="2026-09-09"),
        "2026-10-07",
    ) == ("READY_REALIZED", "2026-09-09")


def test_abstention_is_separately_labelled_and_starts_after_recording():
    value = ledger(
        intent_status="NO_ACTION",
        intent_action="NO_TRADE",
        position_id=None,
        risk_snapshot={
            "execution_contract": "prospective-next-close.v2",
            "execution_session": "2026-09-02",
        },
    )
    status, due = outcomes.learning_status(run("NO_TRADE"), value, "2026-09-10")
    assert status == "PENDING_MATURITY" and due == "2026-10-01"


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


def test_cold_start_rotates_untried_arms_while_outcomes_are_pending():
    state = {"bias_score": 0.5, "signal_components": {"technical": 0.4}}
    first = bandit.select(state, ["LONG_STOCK", "NO_TRADE"], decision_key="one")
    chosen = first["selected"]["action"]
    second = bandit.select(
        state, ["LONG_STOCK", "NO_TRADE"], decision_key="two", trial_counts={chosen: 1}
    )
    assert second["selected"]["action"] != chosen
    assert second["selected"]["observations"] == 0


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
