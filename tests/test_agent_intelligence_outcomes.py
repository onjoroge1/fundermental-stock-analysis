from stock_machine.agent_intelligence import outcomes


def test_drawdown_uses_observed_path():
    value=outcomes._drawdown([1.0,1.10,.99,1.05])
    assert round(value,2)==-10.0


def test_stock_outcome_scores_long_and_short_from_adjusted_prices(monkeypatch):
    rows=[
        {"date":"2026-09-01","adj_close":100.0,"close":100.0},
        {"date":"2026-09-02","adj_close":90.0,"close":90.0},
        {"date":"2026-09-03","adj_close":110.0,"close":110.0},
    ]
    monkeypatch.setattr(outcomes.db,"fetch_prices",lambda conn,ticker,as_of:rows)
    long=outcomes._stock_outcome(object(),"AAPL","LONG_STOCK","2026-09-01","2026-09-03")
    short=outcomes._stock_outcome(object(),"AAPL","SHORT_STOCK","2026-09-01","2026-09-03")
    assert long["gross_return_pct"]==10.0
    assert long["max_drawdown_pct"]==-10.0
    assert short["gross_return_pct"]==-10.0
    assert short["max_drawdown_pct"]==-20.0
    assert long["costs_pct"]==.20


def test_no_trade_has_zero_capital_and_zero_reward_inputs():
    value=outcomes._stock_outcome(object(),"AAPL","NO_TRADE","2026-09-01","2026-09-30")
    assert value["gross_return_pct"]==0
    assert value["capital_used_pct"]==0
    assert value["turnover_pct"]==0
    assert value["costs_pct"]==0


def test_score_matured_records_only_matured_stock_action(monkeypatch):
    from stock_machine import research_store
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): return False

    run={"ticker":"AAPL","decision_id":"d1",
         "state":{"as_of":"2026-09-01"},
         "bandit":{"selected":{"action":"LONG_STOCK"}}}
    monkeypatch.setattr(outcomes.db,"connect",lambda:Conn())
    monkeypatch.setattr(research_store,"records",lambda conn,kind,limit:[{"payload":run}])
    monkeypatch.setattr(research_store,"get",lambda *a,**k:None)
    monkeypatch.setattr(outcomes,"latest_completed_session",lambda:"2026-10-15")
    monkeypatch.setattr(outcomes,"session_offset",lambda entry,n:"2026-09-30")
    monkeypatch.setattr(outcomes,"_stock_outcome",lambda *a,**k:{
        "status":"MATURED","gross_return_pct":3.0,"max_drawdown_pct":-1.0,
        "capital_used_pct":10.0,"turnover_pct":20.0,"costs_pct":.2,
        "entry_date":"2026-09-01","exit_date":"2026-09-30"})
    monkeypatch.setattr(outcomes,"record_outcome",lambda ticker,decision_id,outcome:{
        "reward_record":{"reward":{"reward":.2}}})
    result=outcomes.score_matured()
    assert result["scored"]==1
    assert result["results"][0]["status"]=="SCORED"
