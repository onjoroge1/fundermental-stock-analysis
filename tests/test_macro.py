from stock_machine.macro import features_as_of, interaction_features


def _rows(values, start_day=1, lag=0):
    out = []
    for i, value in enumerate(values):
        day = start_day + i
        out.append({
            "observation_date": f"2026-01-{day:02d}",
            "available_at": f"2026-01-{day + lag:02d}",
            "value": float(value),
        })
    return out


def test_macro_asof_respects_available_at_not_observation_date():
    series = {
        "VIXCLS": [
            {"observation_date": "2026-01-01", "available_at": "2026-01-01", "value": 15.0},
            {"observation_date": "2026-01-02", "available_at": "2026-01-03", "value": 40.0},
        ],
        "DGS2": [], "DGS10": [], "BAMLH0A0HYM2": [],
    }
    f = features_as_of(series, "2026-01-02")["features"]
    assert f["vix_level"] == 15.0


def test_curve_and_credit_are_point_in_time():
    series = {
        "VIXCLS": [],
        "DGS2": [{"observation_date": "2026-01-01", "available_at": "2026-01-02", "value": 4.0}],
        "DGS10": [{"observation_date": "2026-01-01", "available_at": "2026-01-02", "value": 4.5}],
        "BAMLH0A0HYM2": [{"observation_date": "2026-01-01", "available_at": "2026-01-02", "value": 3.2}],
    }
    before = features_as_of(series, "2026-01-01")["features"]
    after = features_as_of(series, "2026-01-02")["features"]
    assert before["has_curve"] == 0.0
    assert after["curve_10y2y"] == 0.5
    assert after["hy_oas"] == 3.2


def test_macro_interactions_create_cross_sectional_variation():
    base = {
        "macro": {"features": {
            "vix_level": 30.0, "vix_change_20": 5.0,
            "hy_oas": 4.0, "hy_oas_change_20": 0.5,
            "curve_10y2y": 0.4, "curve_change_63": 0.2,
        }},
        "regime": {"features": {"market_vol_21": 0.2, "sector_vs_spy_63": 0.03}},
        "components": {"valuation": 60.0, "profitability": 70.0,
                       "financial_health": 65.0, "growth": 75.0},
    }
    a = {**base, "factors": {"momentum_12m_pct": 20.0}}
    b = {**base, "factors": {"momentum_12m_pct": -10.0}}
    ia = interaction_features(a)
    ib = interaction_features(b)
    assert ia["vix_x_momentum"] != ib["vix_x_momentum"]
    assert ia["curve_change_x_momentum"] != ib["curve_change_x_momentum"]
