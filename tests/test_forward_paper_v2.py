import pytest

import stock_machine.forward_paper_v2 as fp


def _observations():
    rows = []
    for i in range(10):
        signal = float(i)
        rows.append({
            "ticker": f"T{i}",
            "sector": "Tech" if i < 5 else "Consumer",
            "as_of": "2026-08-30",
            "composite": 50 + signal,
            "components": {
                "growth": 50 + signal,
                "profitability": 50 + signal,
                "earnings_quality": 50 + signal,
                "financial_health": 50,
                "capital_allocation": 50,
                "valuation": 50 + signal,
            },
            "factors": {
                "earnings_yield_pct": signal,
                "fcf_yield_pct": signal,
                "revenue_yoy_pct": signal,
                "roic_pct": signal,
                "momentum_12m_pct": signal,
            },
            "expectations": {
                "eps_revision_pct": signal,
                "revenue_revision_pct": signal,
                "latest_eps_surprise_pct": signal,
                "trailing_4q_eps_surprise_pct": signal,
            },
            "forward": {"fwd_3m_pct": None},
        })
    return rows


def _lab(run_id="slv2_aaa"):
    eligible = {
        "promotion": {"status": "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW"}
    }
    return {
        "run_id": run_id,
        "panel_hash": "panel123",
        "result": {
            "schema_version": "strategy_lab.v2",
            "modes": {
                "long_only": {"strategies": {"value_quality": eligible}},
                "long_short": {"strategies": {"value_quality": eligible}},
            },
        },
    }


def _prices(market_date="2026-08-28"):
    return {
        f"T{i}": {"market_date": market_date, "adj_close": 100.0 + i,
                    "close": 100.0 + i}
        for i in range(10)
    }


def test_build_long_only_contract_freezes_universe_and_one_market_date():
    contract = fp.build_contract(
        _lab(), "value_quality", "long_only", _observations(), _prices()
    )
    assert contract["entry_market_date"] == "2026-08-28"
    assert len(contract["longs"]) >= 2
    assert contract["shorts"] == []
    assert len(contract["frozen_eligible_universe"]) == 10
    assert contract["control"] == "frozen_equal_weight_universe"
    assert contract["policy_signals"] == ["factors.earnings_yield_pct", "factors.roic_pct"]


def test_build_contract_aborts_mixed_entry_dates():
    prices = _prices()
    prices["T0"]["market_date"] = "2026-08-27"
    with pytest.raises(ValueError, match="one exact market date"):
        fp.build_contract(
            _lab(), "value_quality", "long_only", _observations(), prices
        )


def test_identity_does_not_reset_for_new_lab_run_or_new_entry_price():
    first = fp.build_contract(
        _lab("slv2_old"), "value_quality", "long_short",
        _observations(), _prices("2026-08-28"),
    )
    second = fp.build_contract(
        _lab("slv2_new"), "value_quality", "long_short",
        _observations(), _prices("2026-08-29"),
    )
    assert first["lab_run_id"] != second["lab_run_id"]
    assert first["entry_market_date"] != second["entry_market_date"]
    assert fp._identity_hash(first) == fp._identity_hash(second)


def test_ineligible_policy_cannot_be_frozen():
    lab = _lab()
    lab["result"]["modes"]["long_only"]["strategies"]["value_quality"] = {
        "promotion": {"status": "REJECTED"}
    }
    with pytest.raises(ValueError, match="not eligible"):
        fp.build_contract(
            lab, "value_quality", "long_only", _observations(), _prices()
        )


def test_build_mark_requires_complete_same_date(monkeypatch):
    contract = fp.build_contract(
        _lab(), "value_quality", "long_short", _observations(), _prices()
    )
    cohort = {"cohort_id": "c1", "contract": contract}
    values = {
        ticker: price * (1.10 if ticker in contract["longs"] else 0.95)
        for ticker, price in contract["entry_adjusted_close"].items()
    }
    monkeypatch.setattr(
        fp, "_exact_price",
        lambda conn, ticker, market_date: values.get(ticker),
    )
    mark = fp.build_mark(object(), cohort, "2026-10-30")
    assert mark["coverage"]["complete"] is True
    assert mark["net_return_pct"] > 0
    assert mark["control_return_pct"] == 0.0

    missing = contract["longs"][0]
    monkeypatch.setattr(
        fp, "_exact_price",
        lambda conn, ticker, market_date: None if ticker == missing else values.get(ticker),
    )
    with pytest.raises(ValueError, match="mark aborted"):
        fp.build_mark(object(), cohort, "2026-10-31")


def _marks(n=40, latest_excess=8.0):
    rows = []
    for i in range(n):
        value = latest_excess * (i + 1) / n
        rows.append({
            "market_date": f"2027-01-{(i % 28) + 1:02d}",
            "net_return_pct": value,
            "control_return_pct": 0.0,
            "excess_return_pct": value,
            "coverage": {"complete": True},
        })
    return rows


def test_review_gate_requires_age_marks_positive_excess_and_drawdown():
    cohort = {
        "cohort_id": "c1", "policy_name": "value_quality",
        "mode": "long_short", "entry_market_date": "2026-08-28",
    }
    collecting = fp.status(cohort, _marks(10), today="2026-10-01")
    assert collecting["status"] == "COLLECTING"

    eligible = fp.status(cohort, _marks(40), today="2027-01-15")
    assert eligible["status"] == "REVIEW_ELIGIBLE"
    assert all(eligible["gates"].values())


def test_mature_bad_forward_evidence_fails():
    cohort = {
        "cohort_id": "c1", "policy_name": "value_quality",
        "mode": "long_short", "entry_market_date": "2026-08-28",
    }
    bad = _marks(40, latest_excess=-25.0)
    result = fp.status(cohort, bad, today="2027-01-15")
    assert result["status"] == "FAILED"
    assert result["gates"]["positive_cumulative_excess"] is False
