from datetime import date, timedelta
from stock_machine.agent_intelligence import option_bridge


def test_expiration_selects_bounded_near_target():
    d=date.today()+timedelta(days=44)
    rows=[{"month":"X","standard":d.strftime("%Y%m%d")}]
    value=option_bridge._expiration(rows)
    assert value["dte"]==44


def test_direction_types_are_defined_risk_templates_only():
    names={x.value for x in option_bridge.DIRECTION_TYPES["BULLISH"]}
    assert names=={"bull_call_debit_spread","bull_put_credit_spread"}
    names={x.value for x in option_bridge.DIRECTION_TYPES["BEARISH"]}
    assert names=={"bear_put_debit_spread","bear_call_credit_spread"}
    assert {x.value for x in option_bridge.DIRECTION_TYPES["NEUTRAL"]}=={"iron_condor"}


def test_option_paper_entry_is_append_only_and_replay_safe(monkeypatch):
    from stock_machine.agent_intelligence import option_paper
    stored={}
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): return False
    monkeypatch.setattr(option_paper.db,"connect",lambda:Conn())
    monkeypatch.setattr(option_paper.research_store,"get",
                        lambda conn,kind,key: stored.get((kind,key)))
    def save(conn,kind,key,payload,ticker):
        row={"record_id":"r1","payload":payload}
        stored[(kind,key)]=row
        return {"record_id":"r1","content_hash":"h","payload":payload,"recorded_at":"now"}
    monkeypatch.setattr(option_paper.research_store,"save",save)
    candidate={"candidate_id":"c1","strategy_type":"bull_call_debit_spread",
               "expiration":"2026-11-20","spot_price":100,
               "legs":[],"payoff":{"defined_risk":True,"max_loss":500}}
    first=option_paper.open_entry("AAPL","d1",candidate)
    second=option_paper.open_entry("AAPL","d1",candidate)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["candidate_id"]=="c1"
    assert second["broker_submission"] is False
