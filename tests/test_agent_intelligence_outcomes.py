import pytest
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
    assert round(short["max_drawdown_pct"],4)==-18.1818


def test_no_trade_is_not_a_learned_arm():
    import pytest
    with pytest.raises(ValueError,match="OUTCOME_ACTION_NOT_LEARNED"):
        outcomes._stock_outcome(object(),"AAPL","NO_TRADE","2026-09-01","2026-09-30")


def counterfactual_run(decision_id="d1", action="NO_TRADE"):
    return {"mode":"PAPER","learning_contract":outcomes.CONTRACT,"ticker":"AAPL","decision_id":decision_id,
            "state":{"as_of":"2026-09-01","paper_eligible":True},
            "learning":{"contract":outcomes.CONTRACT,"execution_session":"2026-09-02","due_session":"2026-09-30"},
            "bandit":{"selected":{"action":action}}}


def patch_scan(monkeypatch, run, completed="2026-10-15"):
    from contextlib import nullcontext
    from stock_machine import research_store
    class Conn:
        autocommit=False
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def transaction(self): return nullcontext()
    seen={}
    def candidates(conn,limit,completed,contract):
        seen.update(completed=completed,contract=contract)
        return [{"payload":run}]
    monkeypatch.setattr(outcomes.db,"connect",lambda:Conn())
    monkeypatch.setattr(research_store,"outcome_candidates",candidates)
    monkeypatch.setattr(research_store,"get",lambda *a,**k:None)
    monkeypatch.setattr(outcomes,"latest_completed_session",lambda:completed)
    return seen


def test_score_matured_learns_both_directions_even_when_the_agent_abstained(monkeypatch):
    seen=patch_scan(monkeypatch,counterfactual_run(action="NO_TRADE"))
    windows=[]
    def path(conn,ticker,action,entry,due):
        windows.append((action,entry,due))
        return {"status":"MATURED","gross_return_pct":3.0 if action=="LONG_STOCK" else -3.0,
                "max_drawdown_pct":-1.0,"entry_date":entry,"exit_date":due}
    monkeypatch.setattr(outcomes,"_stock_outcome",path)
    captured={}
    def record(ticker,decision_id,arm_outcomes,conn=None):
        captured.update(arm_outcomes)
        return {"reward_record":{"arms":{a:{"reward":{"reward":1.0}} for a in arm_outcomes}}}
    monkeypatch.setattr(outcomes,"record_counterfactual",record)
    result=outcomes.score_matured()
    assert seen=={"completed":"2026-10-15","contract":outcomes.CONTRACT}
    assert result["scored"]==1 and result["results"][0]["selected_action"]=="NO_TRADE"
    # SPY over the same window, then both directions of the stock.
    assert windows==[("LONG_STOCK","2026-09-02","2026-09-30")]*2+[("SHORT_STOCK","2026-09-02","2026-09-30")]
    assert captured["LONG_STOCK"]["residual_return_pct"]==pytest.approx(3.0-1.0*3.0)
    assert captured["SHORT_STOCK"]["beta_basis"]=="default_beta"
    assert captured["LONG_STOCK"]["costs_pct"]==.2
    assert captured["SHORT_STOCK"]["learning_basis"]=="PROSPECTIVE_COUNTERFACTUAL_V1"


def test_blocked_matured_outcome_reports_attention(monkeypatch):
    patch_scan(monkeypatch,counterfactual_run("blocked","LONG_STOCK"))
    def fail(*args):
        raise ValueError("OUTCOME_PATH_SESSION_MISSING")
    monkeypatch.setattr(outcomes,"_stock_outcome",fail)
    result=outcomes.score_matured()
    assert result["status"]=="ATTENTION" and result["blocked"]==1
    assert result["scored"]==0


def test_loop_health_preserves_blocked_scan_after_job_completes():
    from stock_machine.admin_panel.operations import _learning_loop_health
    class Rows:
        def fetchall(self):
            return [("agent_intelligence_outcomes",None,"SUCCEEDED","2026-10-07",
                     "2026-10-07",1,None,{"status":"ATTENTION","blocked":1,"scored":0})]
    class Conn:
        def execute(self,*args): return Rows()
    result=_learning_loop_health(Conn())
    assert result["status"]=="ATTENTION"
    assert result["latest_outcome_scan"]["result_summary"]["blocked"]==1


def test_blocked_outcomes_record_a_once_per_session_retry(monkeypatch):
    patch_scan(monkeypatch,counterfactual_run("blocked","LONG_STOCK"))
    def fail(*args):
        raise ValueError("OUTCOME_PATH_SESSION_MISSING")
    monkeypatch.setattr(outcomes,"_stock_outcome",fail)
    checks=[]
    monkeypatch.setattr(outcomes,"record_check",lambda row,completed,conn=None:checks.append((row["decision_id"],completed)) or "NEXT_SESSION")
    result=outcomes.score_matured()
    assert checks==[("blocked","2026-10-15")]
    assert result["results"][0]["retry"]=="NEXT_SESSION"


def test_counterfactual_records_sector_relative_return_without_blocking(monkeypatch):
    paths={"SPY":2.0,"XLK":5.0,"AAPL":8.0}
    def outcome(conn,ticker,action,entry,due):
        if ticker=="XLB":
            raise ValueError("OUTCOME_PATH_SESSION_MISSING")
        sign=1 if action=="LONG_STOCK" else -1
        return {"gross_return_pct":sign*paths[ticker],"max_drawdown_pct":-1.0,"entry_date":entry,"exit_date":due}
    monkeypatch.setattr(outcomes,"_stock_outcome",outcome)
    value=outcomes.counterfactual_outcomes(None,"AAPL","2026-09-02","2026-09-30",(1.0,"x"),"XLK")
    assert value["LONG_STOCK"]["sector_relative_return_pct"]==pytest.approx(3.0)
    assert value["SHORT_STOCK"]["sector_relative_return_pct"]==pytest.approx(-3.0)
    assert value["LONG_STOCK"]["residual_return_pct"]==pytest.approx(6.0)   # the learning label is unchanged
    missing=outcomes.counterfactual_outcomes(None,"AAPL","2026-09-02","2026-09-30",(1.0,"x"),"XLB")
    assert missing["LONG_STOCK"]["sector_relative_return_pct"] is None
