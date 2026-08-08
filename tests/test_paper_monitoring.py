from stock_machine.monitoring import GENERIC_RULES, RULES, check_bundle
from stock_machine.paper import DIRECTION


def _bundle(ticker, **metrics):
    groups = {"growth": {}, "profitability": {}, "earnings_quality": {},
              "financial_health": {}, "valuation": {}}
    ttm_fields = {}
    for path, v in metrics.items():
        group, _, name = path.partition("__")
        if group == "ttm":
            ttm_fields[name] = v
        else:
            groups[group][name] = v
    return {"company": {"ticker": ticker},
            "derived_metrics": groups,
            "financial_history": {"ttm": {"fields": ttm_fields}}}


def test_breach_detected_below_threshold():
    b = _bundle("UBER", growth__revenue_yoy_pct=5.0,
                profitability__fcf_margin_pct=18.0)
    breaches = check_bundle(b)
    ids = {x["rule_id"] for x in breaches}
    assert "rev_lt_8" in ids and "fcfm_lt_12" not in ids


def test_no_breach_when_healthy():
    b = _bundle("UBER", growth__revenue_yoy_pct=14.0,
                profitability__fcf_margin_pct=18.0)
    assert check_bundle(b) == []


def test_missing_data_is_not_a_breach():
    assert check_bundle(_bundle("UBER")) == []


def test_ttm_metric_path():
    b = _bundle("F", ttm__free_cash_flow=5e9)
    assert any(x["rule_id"] == "fcf_lt_6b" for x in check_bundle(b))
    b2 = _bundle("F", ttm__free_cash_flow=9e9)
    assert not any(x["rule_id"] == "fcf_lt_6b" for x in check_bundle(b2))


def test_generic_rule_applies_to_unlisted_ticker():
    b = _bundle("ZZZZ",
                earnings_quality__receivables_growth_minus_revenue_growth_pct=30.0)
    assert any(x["rule_id"] == "generic_recgap_gt_25" for x in check_bundle(b))


def test_thesis_positive_breach_tsla():
    # TSLA margin recovery above 10% invalidates the bear premise — must flag
    b = _bundle("TSLA", profitability__operating_margin_pct=12.0)
    assert any(x["rule_id"] == "om_gt_10" for x in check_bundle(b))


def test_rules_reference_valid_ops():
    for rules in list(RULES.values()) + [GENERIC_RULES]:
        for r in rules:
            assert r["op"] in ("lt", "gt")
            assert isinstance(r["threshold"], (int, float))


def test_direction_mapping():
    assert DIRECTION["ATTRACTIVE"] == "long"
    assert DIRECTION["UNATTRACTIVE"] == "short"
    assert "WATCH" not in DIRECTION  # abstention is a position
