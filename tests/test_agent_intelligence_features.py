from stock_machine.agent_intelligence import features


def rows(n=260, start=100.0, drift=.002, volume=1_000_000):
    out=[]
    price=start
    for i in range(n):
        # Small deterministic variation keeps covariance/beta mathematically
        # defined while preserving the requested trend.
        variation = .0004 if i % 3 == 0 else (-.0002 if i % 3 == 1 else 0.0)
        price *= 1 + drift + variation
        out.append({"date":f"2026-{1+(i//28):02d}-{1+(i%28):02d}",
                    "open":price*.995,"high":price*1.01,"low":price*.99,
                    "close":price,"adj_close":price,"volume":volume})
    return out


def test_feature_engine_detects_uptrend_and_positive_momentum():
    s=rows()
    m=rows(drift=.0005)
    value=features.compute(s, market_rows=m)
    assert value["status"]=="OK"
    assert value["classification"]["trend"]=="UP"
    assert value["features"]["momentum_63"] > 0
    assert value["features"]["relative_momentum_63_vs_spy"] > 0
    assert value["features"]["rsi14"] > 50
    assert value["features"]["atr14_pct"] > 0


def test_feature_engine_is_asof_bounded():
    s=rows(100)
    cutoff=s[-10]["date"]
    value=features.compute(s, as_of=cutoff)
    assert value["as_of"]==cutoff
    assert value["observations"]==91


def test_feature_engine_reports_partial_without_enough_history():
    value=features.compute(rows(30))
    assert value["status"]=="PARTIAL"
    assert value["features"]["momentum_63"] is None


def test_beta_corr_aligns_common_dates():
    s=rows(100,drift=.001)
    m=rows(100,drift=.001)
    value=features.compute(s,market_rows=m)
    assert value["features"]["beta_63_vs_spy"] is not None
    assert value["features"]["correlation_63_vs_spy"] > .99
