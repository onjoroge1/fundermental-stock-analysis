from stock_machine.features.scoring import (BASE_THRESHOLDS,
                                            SECTOR_OVERRIDES, _scale,
                                            score_all, score_profitability,
                                            thresholds_for)


def test_unknown_sector_uses_general_profile():
    t, profile, overridden = thresholds_for(None)
    assert profile == "general" and overridden == []
    assert t is BASE_THRESHOLDS
    t2, p2, _ = thresholds_for("Banks")  # not a defined profile
    assert p2 == "general"


def test_auto_margins_score_fairly_under_auto_profile():
    """A 7% operating margin automaker is mid-pack for autos but poor for
    the general profile — the sector adapter must re-anchor it."""
    p = {"gross_margin_pct": 15.0, "operating_margin_pct": 7.0,
         "roic_pct": 8.0, "fcf_margin_pct": 3.0}
    t_auto, _, _ = thresholds_for("Automobiles")
    t_gen, _, _ = thresholds_for(None)
    assert score_profitability(p, t_auto) > score_profitability(p, t_gen) + 15


def test_software_growth_bar_is_higher():
    t_sw, _, _ = thresholds_for("Software & Internet")
    t_gen, _, _ = thresholds_for(None)
    # 10% growth: mediocre for software, decent for a general company
    assert _scale(10.0, t_sw["revenue_yoy_pct"]) < _scale(10.0, t_gen["revenue_yoy_pct"])


def test_score_all_reports_profile():
    out = score_all({"growth": {"revenue_yoy_pct": 5.0}}, None, "Automobiles")
    sp = out["scoring_profile"]
    assert sp["profile"] == "Automobiles"
    assert "operating_margin_pct" in sp["sector_adjusted_metrics"]
    out2 = score_all({"growth": {"revenue_yoy_pct": 5.0}})
    assert out2["scoring_profile"]["profile"] == "general"


def test_overrides_reference_only_known_metrics():
    from stock_machine.features.scoring import PROFILE_EXTENSION_METRICS
    for sector, over in SECTOR_OVERRIDES.items():
        unknown = (set(over) - set(BASE_THRESHOLDS)
                   - PROFILE_EXTENSION_METRICS)
        assert not unknown, f"{sector} overrides unknown metrics: {unknown}"


def test_breakpoints_are_sorted_and_scores_in_range():
    for table in [BASE_THRESHOLDS] + list(SECTOR_OVERRIDES.values()):
        for key, points in table.items():
            xs = [x for x, _ in points]
            assert xs == sorted(xs), f"{key} breakpoints unsorted"
            assert all(0 <= y <= 100 for _, y in points), f"{key} score range"
