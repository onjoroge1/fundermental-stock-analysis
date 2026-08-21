from stock_machine.strategy_screen import generate


def _lab(status="PAPER_ELIGIBLE"):
    return {
        "status": "OK",
        "strategies": {
            "value_quality": {
                "kind": "multi_factor",
                "promotion": {"status": status},
            },
            "earnings_yield": {
                "kind": "baseline",
                "promotion": {"status": "BASELINE"},
            },
        },
    }


def _rows(count=10):
    return [{
        "ticker": f"T{i:02d}",
        "composite": i,
        "components": {"profitability": i},
        "factors": {"earnings_yield_pct": i, "roic_pct": i,
                    "revenue_yoy_pct": i, "momentum_12m_pct": i},
        "price": 100 + i,
        "price_date": "2026-08-21",
    } for i in range(count)]


def _ready(rows):
    return {row["ticker"]: {"status": "READY", "trade_eligible": True,
                            "warnings": [], "blockers": []}
            for row in rows}


def test_screen_selects_only_promoted_policy_top_quintile():
    rows = _rows()
    result = generate(_lab(), rows, _ready(rows), as_of="2026-08-21")

    assert result["status"] == "OK"
    assert result["execution_status"] == "PAPER_ONLY"
    assert list(result["policies"]) == ["value_quality"]
    picks = result["policies"]["value_quality"]["candidates"]
    assert [row["ticker"] for row in picks] == ["T09", "T08"]
    assert sum(row["target_weight"] for row in picks) == 1
    assert picks[0]["raw_signals"] == {
        "factors.earnings_yield_pct": 9.0, "factors.roic_pct": 9.0,
    }


def test_screen_blocks_when_no_policy_passed_evaluation():
    rows = _rows()
    result = generate(_lab("REJECTED"), rows, _ready(rows))
    assert result["status"] == "BLOCKED"
    assert result["policies"] == {}


def test_screen_excludes_blocked_data_before_ranking():
    rows = _rows()
    readiness = _ready(rows)
    readiness["T09"] = {"status": "BLOCKED", "trade_eligible": False,
                        "blockers": ["prices: stale"], "warnings": []}
    result = generate(_lab(), rows, readiness)

    picks = result["policies"]["value_quality"]["candidates"]
    assert "T09" not in {row["ticker"] for row in picks}
    assert result["universe"]["excluded"] == [
        {"ticker": "T09", "reason": "prices: stale"},
    ]


def test_screen_blocks_when_quality_gate_leaves_too_few_names():
    rows = _rows(8)
    readiness = _ready(rows)
    readiness["T00"]["trade_eligible"] = False
    readiness["T00"]["blockers"] = ["filings: missing"]
    result = generate(_lab(), rows, readiness)
    assert result["status"] == "BLOCKED"
    assert "need 8+" in result["reason"]


def test_screen_fails_closed_when_promoted_signal_has_no_coverage():
    rows = _rows()
    for row in rows:
        row["factors"]["roic_pct"] = None
    result = generate(_lab(), rows, _ready(rows))
    assert result["status"] == "BLOCKED"
    assert "signal coverage" in result["reason"]
