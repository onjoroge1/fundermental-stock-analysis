from stock_machine.agent_intelligence.state import assemble
from stock_machine.agent_intelligence.strategy_router import route


def bundle(score=80):
    return {"fundamental_scores":{"composite_score":score},
            "market_snapshot":{"price_date":"2026-09-18"},
            "data_quality":{"status":"PASS","financial_integrity":{"status":"VERIFIED"}}}


def tech(mom=.15,share=.75,rel=.08):
    return {"status":"OK","features":{"momentum_63":mom,"relative_momentum_63_vs_spy":rel},
            "classification":{"trend_positive_vote_share":share}}


def news(pressure=.2,negative=False):
    return {"status":"OK","features":{"signed_event_pressure":pressure,
            "high_materiality_negative":negative}}


def option(strategy="bull_call_debit_spread",loss=600,ranking=80):
    return {"candidate_id":"x","strategy_type":strategy,
            "payoff":{"defined_risk":True,"max_loss":loss},
            "liquidity":{"passed":True,"score":.9},
            "ranking":{"total":ranking}}


def test_state_combines_fundamental_technical_news_transparently():
    state=assemble("AAPL",bundle(),tech(),news())
    assert state["direction"]=="BULLISH"
    assert state["paper_eligible"] is True
    assert state["signal_components"]["fundamental"] > 0


def test_high_materiality_negative_news_blocks_paper_state():
    state=assemble("AAPL",bundle(),tech(),news(-.8,True))
    assert "HIGH_MATERIALITY_NEGATIVE_HEADLINE_CONTEXT" in state["blockers"]
    assert state["paper_eligible"] is False


def test_router_can_prefer_defined_risk_option_over_stock():
    state=assemble("AAPL",bundle(),tech(),news())
    result=route(state,[option()],max_risk_usd=1000)
    assert result["selected"]["instrument"]=="OPTION"
    assert result["selected"]["strategy_type"]=="bull_call_debit_spread"
    assert result["broker_submission"] is False


def test_router_forces_no_trade_on_state_blocker():
    state=assemble("AAPL",bundle(),tech(),news(-.9,True))
    result=route(state,[option()])
    assert result["selected"]["action"]=="NO_TRADE"
