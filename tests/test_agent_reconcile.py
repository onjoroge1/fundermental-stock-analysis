"""Executed paper positions must agree with their counterfactual learning labels."""

import pytest

from stock_machine.agent_intelligence import reconcile


def position(**overrides):
    return {
        "position_id": "p1", "ticker": "HIMS", "side": "LONG", "source_decision_id": "d1",
        "entry_market_date": "2026-09-17", "exit_market_date": "2026-10-15",
        # +10% gross on $926 notional, less $0.926 entry and $1.0186 exit costs.
        "entry_notional_usd": 926.0, "entry_cost_usd": 0.926, "exit_cost_usd": 1.0186,
        "realized_pnl_usd": 92.6 - 0.926 - 1.0186, **overrides,
    }


def label(gross=10.0, entry="2026-09-17", exit_="2026-10-15", arm="LONG_STOCK"):
    return {"learning_basis": "PROSPECTIVE_COUNTERFACTUAL_V1",
            "arms": {arm: {"outcome": {"gross_return_pct": gross, "entry_date": entry, "exit_date": exit_}}}}


def test_matching_position_adds_costs_back_before_comparing():
    row = reconcile.classify(position(), label())
    assert row["status"] == "MATCHED"
    assert row["realized_gross_return_pct"] == pytest.approx(10.0)


def test_short_positions_compare_against_the_short_label():
    short = position(side="SHORT", realized_pnl_usd=-92.6 - 0.926 - 1.0186)
    assert reconcile.classify(short, label(-10.0, arm="SHORT_STOCK"))["status"] == "MATCHED"
    assert reconcile.classify(short, label(-10.0, arm="LONG_STOCK"))["status"] == "INVALID_INPUTS"


@pytest.mark.parametrize("lab,status", [
    (label(exit_="2026-10-14"), "LATE_EXIT"),
    (label(entry="2026-09-16"), "ENTRY_MISMATCH"),
    (label(gross=9.9), "RETURN_MISMATCH"),
    (label(gross=10.04), "MATCHED"),
    (None, "AWAITING_LABEL"),
])
def test_disagreements_are_classified(lab, status):
    assert reconcile.classify(position(), lab)["status"] == status


def test_missing_ledger_values_are_invalid_not_matched():
    assert reconcile.classify(position(realized_pnl_usd=None), label())["status"] == "INVALID_INPUTS"
    assert reconcile.classify(position(entry_notional_usd=0.0), label())["status"] == "INVALID_INPUTS"
