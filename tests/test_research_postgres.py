"""Real PostgreSQL invariants in an isolated schema; never production data."""
import os
from uuid import uuid4

import pytest

from stock_machine import db, research_store
from tests.test_db_integrity import migration_sql
from tests.test_research_integrity import source_bundle


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not configured")
    import psycopg
    schema = "test_research_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")
    try:
        with connect() as conn:
            conn.execute(migration_sql("head"))
            conn.execute("INSERT INTO companies(ticker,cik,legal_name) VALUES ('VZ','0000732712','Test VZ fixture')")
        monkeypatch.setattr(db, "connect", connect)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_evidence_append_only_and_conflicting_key_rolls_back(pg):
    import psycopg
    with pg() as conn:
        first = research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": True})
    with pg() as conn:
        assert research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": True}) == first
    with pytest.raises(ValueError), pg() as conn:
        research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": False})
    for sql in ("UPDATE research_evidence_records SET payload='{}'", "DELETE FROM research_evidence_records", "TRUNCATE research_evidence_records"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), pg() as conn:
            conn.execute(sql)
    with pg() as conn:
        assert research_store.get(conn, "EXPERIMENT_PROTOCOL", "test")["payload"] == {"frozen": True}


def test_agent_intelligence_evidence_kinds_are_allowed_by_real_schema(pg):
    kinds = (
        "AGENT_INTELLIGENCE_V2",
        "AGENT_INTELLIGENCE_V2_FAILURE",
        "AGENT_BANDIT_STATE_V1",
        "AGENT_REWARD_V2",
        "AGENT_OPTION_PAPER_V1",
        "AGENT_OPTION_PAPER_OUTCOME_V1",
    )
    for kind in kinds:
        with pg() as conn:
            saved = research_store.save(
                conn, kind, "schema-contract:" + kind, {"kind": kind}, "VZ"
            )
        assert saved["payload"] == {"kind": kind}
    with pg() as conn:
        stored = {
            kind: research_store.get(conn, kind, "schema-contract:" + kind)["payload"]
            for kind in kinds
        }
    assert stored == {kind: {"kind": kind} for kind in kinds}


def test_outcome_limit_excludes_scored_history_before_selecting_candidates(pg):
    # The old oldest-100 scan never advanced beyond a fully scored page.
    with pg() as conn:
        for i in range(105):
            decision = f"scored-{i}"
            research_store.save(conn, "AGENT_INTELLIGENCE_V2", decision, {
                "decision_id": decision, "bandit": {"selected": {"action": "NO_TRADE"}}}, "VZ")
            research_store.save(conn, "AGENT_REWARD_V2", decision, {"reward": 0}, "VZ")
        research_store.save(conn, "AGENT_INTELLIGENCE_V2", "option-done", {
            "decision_id": "option-done", "bandit": {"selected": {"action": "OPTION:CALL"}}}, "VZ")
        research_store.save(conn, "AGENT_OPTION_PAPER_OUTCOME_V1", "option-done", {"status": "MATURED"}, "VZ")
        research_store.save(conn, "AGENT_INTELLIGENCE_V2", "next", {
            "decision_id": "next", "bandit": {"selected": {"action": "LONG_STOCK"}}}, "VZ")
    with pg() as conn:
        values = research_store.outcome_candidates(conn, limit=1)
    assert [r["payload"]["decision_id"] for r in values] == ["next"]


def test_cycle_report_index_evidence_commit_together_and_replay(pg, monkeypatch, source_bundle):
    from stock_machine import research_contract, research_cycle, control_plane
    monkeypatch.setattr(research_contract, "read_inputs", lambda t: (source_bundle, None, None))
    original = control_plane.save_index_row
    def fail(*a, **k):
        raise RuntimeError("deliberate test failure before index commit")
    monkeypatch.setattr(control_plane, "save_index_row", fail)
    with pytest.raises(RuntimeError):
        research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_reports").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM research_evidence_records").fetchone()[0] == 0
    monkeypatch.setattr(control_plane, "save_index_row", original)
    first = research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    replay = research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    assert not first["replayed"] and replay["replayed"]
    assert first["report_id"] == replay["report_id"]
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_reports").fetchone()[0] == 1
        index = conn.execute("SELECT snapshot FROM stock_research_index WHERE ticker='VZ'").fetchone()[0]
        assert index["research_contract"]["snapshot_id"] == first["research_contract"]["snapshot_id"]
        frozen = research_store.latest(conn, "RESEARCH_SNAPSHOT", "VZ")["payload"]
        assert frozen["contract"]["snapshot_id"] == first["research_contract"]["snapshot_id"]
        assert frozen["report"]["claim_validation"]["status"] == "VERIFIED"


def test_repeatable_reader_sees_one_source_report_forecast_view(pg, monkeypatch, source_bundle):
    from stock_machine import bundle, research_contract
    seen = []
    def build(t, *, connection):
        seen.append(connection.execute("SHOW transaction_isolation").fetchone()[0])
        assert connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        return source_bundle
    monkeypatch.setattr(bundle, "build_bundle", build)
    b, r, p = research_contract.read_inputs("VZ")
    assert seen == ["repeatable read"]
    assert b == source_bundle and r is None and p is None


def test_full_bundle_reader_is_read_only_with_all_real_dependencies(pg):
    from stock_machine.research_contract import read_inputs
    from stock_machine.normalization.financial_periods import build_periods
    from pathlib import Path
    import json
    raw = json.loads((Path(__file__).parent / "fixtures/vz_2026q2_source_extract.json").read_text())
    quarters, annual, _ = build_periods(raw)
    with pg() as conn:
        db.replace_periods(conn, "VZ", quarters, annual)
    # No monkeypatched bundle helpers: exercises actual monitoring, peers,
    # base rates, events, snapshots and source-backed financial calculations.
    bundle, report, forecast = read_inputs("VZ")
    assert bundle["company"]["ticker"] == "VZ"
    assert bundle["market_snapshot"]["net_debt"] == 163479000000
    assert bundle["peer_group"]["available"] is False
    assert bundle["peer_group"]["comparison"] == []
    assert bundle["price_implied_expectations"]["status"] == "WITHHELD"
    assert bundle["base_rates"]["status"] == "WITHHELD"
    assert report is None and forecast is None


def test_public_coverage_uses_read_only_index_and_keeps_pending_names(pg, monkeypatch, source_bundle):
    from stock_machine import control_plane, webapp, api_v1
    def forbid_rebuild(*args, **kwargs):
        raise AssertionError("public coverage rebuilt a bundle")
    monkeypatch.setattr(webapp, "_companies_live", forbid_rebuild)
    monkeypatch.setattr(control_plane, "build_index_row", forbid_rebuild)
    with pg() as conn:
        conn.execute("INSERT INTO companies(ticker,cik,legal_name) VALUES ('AAPL','0000320193','Apple')")
        control_plane.save_index_row(conn, "VZ", {"ticker": "VZ", "price": 49.5, "indexed_at": "2026-09-16T19:05:00+00:00"}, commit=False)
    rows = webapp.companies()
    api_rows, generated = api_v1._load_coverage_rows()
    assert rows == api_rows
    assert len(rows) == 2 and generated == "2026-09-16T19:05:00+00:00"
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["index_status"] == "PENDING"
    assert not by_ticker["AAPL"]["research_contract"]["guidance_eligible"]
    assert by_ticker["VZ"]["price"] == 49.5
    with pg() as conn:
        control_plane.coverage_rows(conn)
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"


def test_concurrent_counterfactual_learning_is_serialized_and_replays_do_not_double_count(pg):
    from concurrent.futures import ThreadPoolExecutor
    from stock_machine.agent_intelligence import outcomes
    from stock_machine.agent_intelligence.learning import pooled_state, record_counterfactual
    window={'contract':outcomes.CONTRACT,'execution_session':'2026-09-01','due_session':'2026-09-29'}
    arm_outcomes={a:{'learning_basis':'PROSPECTIVE_COUNTERFACTUAL_V1','gross_return_pct':3. if a=='LONG_STOCK' else -3.,
                     'residual_return_pct':1. if a=='LONG_STOCK' else -1.,
                     'max_drawdown_pct':-1.,'capital_used_pct':.93,'turnover_pct':1.86,'costs_pct':.2,
                     'entry_date':'2026-09-01','exit_date':'2026-09-29'} for a in outcomes.LEARNED_ARMS}
    def run(key):
        return {'ticker':'VZ','decision_id':key,'learning_contract':outcomes.CONTRACT,'learning':window,
                'state':{'paper_eligible':True,'signal_components':{'fundamental':.5}},
                'bandit':{'selected':{'action':'NO_TRADE'}}}
    with pg() as conn:
        for key in ['learn-one','learn-two','learn-three']:
            research_store.save(conn,'AGENT_INTELLIGENCE_V2',key,run(key),'VZ')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:record_counterfactual('VZ',key,arm_outcomes),['learn-one','learn-two']))
    assert not any(r['replayed'] for r in results)
    with pg() as conn:
        state=pooled_state(conn)
        assert state['sequence']==2
        assert all(state['arms'][a]['observations']==2 for a in outcomes.LEARNED_ARMS)
        assert research_store.get(conn,'AGENT_REWARD_V3','learn-one')
    assert record_counterfactual('VZ','learn-one',arm_outcomes)['replayed']
    with pg() as conn:
        assert pooled_state(conn)['arms']['LONG_STOCK']['observations']==2
    shifted={a:{**v,'entry_date':'2026-09-02'} for a,v in arm_outcomes.items()}
    with pytest.raises(ValueError,match='COUNTERFACTUAL_WINDOW_MISMATCH'):
        record_counterfactual('VZ','learn-three',shifted)


def test_direction_evidence_counts_only_matured_decisions_under_the_current_protocol(pg):
    from stock_machine.agent_intelligence import direction
    def intelligence(key, protocol):
        return {'ticker':'VZ','decision_id':key,'learning':{'execution_session':'2026-10-02'},
                'direction_challenger':{'protocol_sha256':protocol,'incumbent':'LONG','challenger':'SHORT'}}
    from stock_machine.agent_intelligence import reward as reward_module
    v=reward_module.VERSION
    reward={'learning_basis':'PROSPECTIVE_COUNTERFACTUAL_V1',
            'arms':{'LONG_STOCK':{'reward':{'reward':-.2,'schema_version':v}},'SHORT_STOCK':{'reward':{'reward':.2,'schema_version':v}}}}
    raw_v4={'learning_basis':'PROSPECTIVE_COUNTERFACTUAL_V1',
            'arms':{'LONG_STOCK':{'reward':{'reward':-.2,'schema_version':'risk-scaled-net-paper-reward.v4'}},
                    'SHORT_STOCK':{'reward':{'reward':.2,'schema_version':'risk-scaled-net-paper-reward.v4'}}}}
    with pg() as conn:
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','counted',intelligence('counted',direction.PROTOCOL_SHA256),'VZ')
        research_store.save(conn,'AGENT_REWARD_V3','counted',reward,'VZ')
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','old-protocol',intelligence('old-protocol','0'*64),'VZ')
        research_store.save(conn,'AGENT_REWARD_V3','old-protocol',reward,'VZ')
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','immature',intelligence('immature',direction.PROTOCOL_SHA256),'VZ')
        # Raw-return (v4) labels are a different target and never count.
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','raw-reward',intelligence('raw-reward',direction.PROTOCOL_SHA256),'VZ')
        research_store.save(conn,'AGENT_REWARD_V3','raw-reward',raw_v4,'VZ')
    with pg() as conn:
        rows=direction.matured_rows(conn)
        value=direction.summary(conn)
    assert rows==[{'execution_session':'2026-10-02','incumbent':'LONG','challenger':'SHORT',
                   'rewards':{'LONG':-.2,'SHORT':.2}}]
    assert value['status']=='PENDING_EVIDENCE' and value['decisions']==1
    assert value['mean_paired_difference']==pytest.approx(.4)


def test_blocked_decisions_wait_one_session_and_cannot_starve_ready_ones(pg):
    from stock_machine.agent_intelligence import outcomes
    def run(key, due):
        return {'ticker':'VZ','decision_id':key,'learning_contract':outcomes.CONTRACT,
                'learning':{'contract':outcomes.CONTRACT,'execution_session':'2026-09-01','due_session':due},
                'state':{'paper_eligible':True},'bandit':{'selected':{'action':'NO_TRADE'}}}
    with pg() as conn:
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','blocked',run('blocked','2026-09-29'),'VZ')
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','ready',run('ready','2026-09-30'),'VZ')
    def first(completed):
        with pg() as conn:
            rows=research_store.outcome_candidates(conn,limit=1,completed=completed,contract=outcomes.CONTRACT)
        return [r['payload']['decision_id'] for r in rows]
    assert first('2026-10-01')==['blocked']
    status=outcomes.record_check({'decision_id':'blocked','ticker':'VZ','status':'BLOCKED_INPUTS',
                                  'reason':'OUTCOME_PATH_SESSION_MISSING'},'2026-10-01')
    assert status=='NEXT_SESSION'
    # The blocked decision no longer occupies the single slot this session...
    assert first('2026-10-01')==['ready']
    # ...and retries after the next completed session.
    assert first('2026-10-02')==['blocked']
    assert outcomes.record_check({'decision_id':'blocked','ticker':'VZ','status':'BLOCKED_INPUTS'},'2026-10-01')=='NEXT_SESSION'


def test_check_falls_back_to_every_pass_before_the_0027_migration(pg):
    from stock_machine.agent_intelligence import outcomes
    with pg() as conn:
        definition=conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='research_evidence_records_kind_check'").fetchone()[0]
        conn.execute("ALTER TABLE research_evidence_records DROP CONSTRAINT research_evidence_records_kind_check")
        conn.execute("ALTER TABLE research_evidence_records ADD CONSTRAINT research_evidence_records_kind_check "
                     + definition.replace(", 'AGENT_OUTCOME_CHECK_V1'::text",""))
        assert "AGENT_OUTCOME_CHECK_V1" not in conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='research_evidence_records_kind_check'").fetchone()[0]
    assert outcomes.record_check({'decision_id':'x','ticker':'VZ'},'2026-10-01')=='EVERY_PASS_CHECK_UNAVAILABLE'
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM research_evidence_records").fetchone()[0]==0


def test_learning_pass_uses_one_connection_and_one_transaction_per_decision(pg, monkeypatch):
    from stock_machine.agent_intelligence import outcomes
    from stock_machine.agent_intelligence.learning import pooled_state
    from stock_machine.market_calendar import session_dates, session_offset
    entry='2026-09-01'; due=session_offset(entry, outcomes.HORIZON_SESSIONS)
    def run(key, eligible=True):
        return {'ticker':'VZ','decision_id':key,'learning_contract':outcomes.CONTRACT,
                'learning':{'contract':outcomes.CONTRACT,'execution_session':entry,'due_session':due},
                'state':{'paper_eligible':eligible,'signal_components':{'fundamental':.5}},
                'bandit':{'selected':{'action':'NO_TRADE'}}}
    with pg() as conn:
        for key in ('ready-1','ready-2'):
            research_store.save(conn,'AGENT_INTELLIGENCE_V2',key,run(key),'VZ')
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','blocked-state',run('blocked-state',False),'VZ')
        research_store.save(conn,'AGENT_INTELLIGENCE_V2','no-path',{**run('no-path'),'ticker':'AAPL'},'AAPL')
        for i,day in enumerate(session_dates(entry,due)):
            conn.execute("INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('VZ',%s,%s,%s)",(day,100+i,100+i))
            conn.execute("INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('SPY',%s,%s,%s)",(day,400+i,400+i))
    opened=[]
    original=db.connect
    monkeypatch.setattr(db,'connect',lambda: opened.append(1) or original())
    monkeypatch.setattr(outcomes,'latest_completed_session',lambda: due)
    result=outcomes.score_matured()
    assert len(opened)==1
    by_key={r['decision_id']:r['status'] for r in result['results']}
    assert by_key=={'ready-1':'SCORED','ready-2':'SCORED','blocked-state':'EXCLUDED_BLOCKED_STATE','no-path':'BLOCKED_INPUTS'}
    with pg() as conn:
        assert pooled_state(conn)['sequence']==2
        # The failed decision rolled back cleanly: a retry check, no reward, no exclusion.
        assert research_store.get(conn,'AGENT_REWARD_V3','no-path') is None
        assert research_store.get(conn,'AGENT_OUTCOME_EXCLUSION_V1','no-path') is None
        assert research_store.get(conn,'AGENT_OUTCOME_CHECK_V1','no-path:'+due)
    opened.clear()
    assert outcomes.score_matured()['results']==[] and len(opened)==1


def test_shadow_pass_uses_one_connection(pg, monkeypatch):
    from stock_machine.agent_intelligence import shadow
    from stock_machine.market_calendar import session_offset
    due=session_offset('2026-09-02',5)
    with pg() as conn:
        for i in range(3):
            research_store.save(conn,shadow.SNAPSHOT,f's{i}:5',{
                'ticker':'VZ','decision_id':f's{i}','origin_session':'2026-09-01','first_future_session':'2026-09-02',
                'horizon_sessions':5,'due_session':due,'components':{},'forecasts':{},'candidate_score':0,
                'baseline_score':0,'agent_action':'NO_TRADE','weight_version':'w'},'VZ')
    opened=[]
    original=db.connect
    monkeypatch.setattr(db,'connect',lambda: opened.append(1) or original())
    monkeypatch.setattr(shadow,'latest_completed_session',lambda *a: due)
    result=shadow.score_matured()
    assert len(opened)==1
    assert [r['status'] for r in result['results']]==['BLOCKED']*3   # no prices: checked, not lost


def test_admin_shadow_view_reads_the_learning_stage_summary_not_every_outcome(pg, monkeypatch):
    from stock_machine.admin_panel import store
    from stock_machine.agent_intelligence import shadow
    with pg() as conn:
        assert shadow.latest_precomputed(conn) is None
        summary=shadow.cumulative_summary(conn)
        assert summary["outcomes"]==0 and summary["reference_counts"]=={}
        store.audit(conn,"agent-scheduler","AGENT_STAGE_FINISHED",
                    {"key":"agent-stage:learning:2026-10-09:1","status":"OK","shadow_cumulative":summary})
        store.audit(conn,"agent-scheduler","AGENT_STAGE_FINISHED",
                    {"key":"agent-stage:paper:2026-10-09:1","status":"OK"})
    def no_scan(conn):
        raise AssertionError("cumulative outcomes must not be re-read per page load")
    monkeypatch.setattr(shadow,"cumulative_rows",no_scan)
    with pg() as conn:
        precomputed=shadow.latest_precomputed(conn)
        assert precomputed["computed_at"]==summary["computed_at"]
        conn.execute("ROLLBACK")
        report=shadow.weekly_summary(conn,cumulative=precomputed)
    assert report["cumulative_source"]["source"]=="LEARNING_STAGE_RECEIPT"
    assert report["weights_promotion_test"]["status"]=="AWAITING_MATURED_OUTCOMES"


def test_learned_direction_needs_owner_approval_and_a_current_passing_test(pg):
    from stock_machine.admin_panel import store
    from stock_machine.agent_intelligence import direction, direction_policy as dp
    def receipt(status, sha=direction.PROTOCOL_SHA256):
        with pg() as conn:
            store.audit(conn,"agent-scheduler","AGENT_STAGE_FINISHED",
                        {"key":"agent-stage:learning:2028-10-02:1","status":"OK",
                         "direction_challenger":{"status":status,"protocol_sha256":sha,"blocks":24}})
    def effective():
        with pg() as conn:
            return dp.effective(conn)
    assert effective()["policy"]=="HEURISTIC" and effective()["reason"]=="NOT_APPROVED"
    # No passing evidence: the owner cannot approve.
    receipt("PENDING_EVIDENCE")
    with pytest.raises(ValueError,match="DIRECTION_TEST_NOT_PASSED"), pg() as conn:
        dp.set_policy(conn,"owner","LEARNED",0,"try early")
    # A pass under a different protocol hash does not count either.
    receipt(dp.PASS, sha="0"*64)
    with pytest.raises(ValueError,match="DIRECTION_TEST_NOT_PASSED"), pg() as conn:
        dp.set_policy(conn,"owner","LEARNED",0,"wrong protocol")
    receipt(dp.PASS)
    with pg() as conn:
        approved=dp.set_policy(conn,"owner","LEARNED",0,"independent review done")
    assert approved["version"]==1 and approved["evaluation_at_approval"]["status"]==dp.PASS
    assert effective()["policy"]=="LEARNED"
    with pytest.raises(ValueError,match="CHANGED_RELOAD"), pg() as conn:
        dp.set_policy(conn,"owner","HEURISTIC",0,"stale page")
    # Later evidence stops passing: decisions revert without owner action.
    receipt("NOT_SUPERIOR")
    assert effective()=={**effective(),"policy":"HEURISTIC","reason":"EVIDENCE_NO_LONGER_PASSES"}
    receipt(dp.PASS)
    assert effective()["policy"]=="LEARNED"
    # An approval made under another protocol no longer applies.
    with pg() as conn:
        store.audit(conn,"owner",dp.EVENT,{"policy":"LEARNED","version":2,"protocol_sha256":"1"*64})
    assert effective()["reason"]=="PROTOCOL_CHANGED_SINCE_APPROVAL"
    # The owner can always revert.
    receipt("NOT_SUPERIOR")
    with pg() as conn:
        reverted=dp.set_policy(conn,"owner","HEURISTIC",2,"revert")
    assert reverted["effective"]["policy"]=="HEURISTIC" and reverted["effective"]["reason"]=="NOT_APPROVED"
