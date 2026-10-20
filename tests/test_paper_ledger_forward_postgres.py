"""The paper-ledger evaluation against real Postgres: register, snapshot, score, mutation, final.

Nothing depends on today's date: every run passes an explicit clock, and the schema's
recorded_at default follows a per-connection setting set to the simulated database time.
Runs happen every night (04:30 UTC after each session), as the scheduled workflow does.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from stock_machine import agent_trading, db, research_store
from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import paper_ledger_forward as plf
from stock_machine.agent_intelligence import strategy_forward as sf
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

DB_NOW = {"t": None}
POLICY_ID = "test-policy"


def at(*args):
    return datetime(*args, tzinfo=timezone.utc)


def run(now):
    DB_NOW["t"] = now
    return plf.run_daily(now=now, clock=lambda: now)


def night_after(session: str) -> datetime:
    d = date.fromisoformat(session) + timedelta(days=1)
    return at(d.year, d.month, d.day, 4, 30)


def nightly(first: str, last: str) -> dict:
    """Run the night after every session from first to last; the last receipt."""
    receipt = None
    for s in session_dates(first, last):
        receipt = run(night_after(s))
    return receipt


@pytest.fixture
def ledger(pg, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    with pg() as conn:
        conn.execute("""ALTER TABLE research_evidence_records ALTER COLUMN recorded_at SET DEFAULT
            COALESCE(NULLIF(current_setting('sm.test_now', true), '')::timestamptz, clock_timestamp())""")
        agent_trading.ensure_schema(conn)
        conn.execute("UPDATE agent_trading_settings SET mode='PAPER',version=2")
        conn.execute("""INSERT INTO operator_audit(actor,event,details,occurred_at) VALUES
            ('production-universe-release','TRADING_MODE_CHANGED','{"before":"RESEARCH","after":"PAPER"}',
             '2026-10-07T12:00:00Z')""")
        conn.execute("INSERT INTO agent_lab_policies(policy_id,content_hash,payload) VALUES (%s,'h','{}')",
                     (POLICY_ID,))
        conn.commit()

    def connect():
        c = pg()
        if DB_NOW["t"] is not None:
            c.execute("SELECT set_config('sm.test_now', %s, false)", (DB_NOW["t"].isoformat(),))
            c.commit()
        return c
    monkeypatch.setattr(db, "connect", connect)
    DB_NOW["t"] = None
    yield pg
    DB_NOW["t"] = None


def decision(conn, ticker, recorded_at) -> str:
    decision_id, sha = str(uuid4()), uuid4().hex + uuid4().hex
    conn.execute("INSERT INTO agent_lab_evidence(input_sha256,payload) VALUES (%s,'{}')", (sha,))
    payload = {"decision_id": decision_id, "ticker": ticker, "status": "RECORDED", "action": "WATCH",
               "input_sha256": sha, "policy_id": POLICY_ID, "mode": "RESEARCH", "execution_status": "NOT_ENABLED",
               "pnl": None, "reward": None}
    conn.execute("""INSERT INTO agent_lab_decisions(decision_id,policy_id,ticker,request_key,observed_at,decided_at,
        recorded_at,status,action,input_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,'RECORDED','WATCH',%s,%s)""",
                 (decision_id, POLICY_ID, ticker, decision_id, recorded_at, recorded_at, recorded_at, sha,
                  json.dumps(payload)))
    return decision_id


def open_position(conn, ticker, entry, recorded_at, *, side="LONG", selector="agent-intelligence.v2",
                  contract="prospective-next-close.v2", price=100.0) -> tuple[str, str]:
    decision_id = decision(conn, ticker, recorded_at)
    intent_id, position_id = str(uuid4()), str(uuid4())
    snapshot = {"selector": {"version": selector}, "execution_contract": contract, "holding_commitment": None}
    conn.execute("""INSERT INTO agent_trade_intents(intent_id,decision_id,ticker,desired_side,action,status,
        market_date,reference_price,target_notional_usd,rationale,risk_snapshot)
        VALUES (%s,%s,%s,%s,%s,'SIMULATED',%s,%s,926,'test',%s)""",
                 (intent_id, decision_id, ticker, side, f"OPEN_{side}", entry, price, json.dumps(snapshot)))
    conn.execute("""INSERT INTO agent_paper_positions(position_id,ticker,side,source_decision_id,source_intent_id,
        entry_market_date,entry_price,paper_units,entry_notional_usd,entry_cost_usd)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,926,0.926)""",
                 (position_id, ticker, side, decision_id, intent_id, entry, price, 926 / price))
    return position_id, decision_id


def close(conn, position_id, exit_, gross_pct, price=100.0):
    pnl = 926 * gross_pct / 100 - 0.926 - 0.926
    conn.execute("""UPDATE agent_paper_positions SET status='CLOSED',exit_market_date=%s,exit_price=%s,
        exit_cost_usd=0.926,realized_pnl_usd=%s,exit_reason='holding limit' WHERE position_id=%s""",
                 (exit_, price * (1 + gross_pct / 100), pnl, position_id))


def label(conn, decision_id, entry, exit_, gross, residual, scale=3.0, side="LONG_STOCK"):
    research_store.save(conn, plf.LABEL_KIND, decision_id, {
        "decision_id": decision_id, "learning_basis": "PROSPECTIVE_COUNTERFACTUAL_V1", "arms": {side: {
            "outcome": {"entry_date": entry, "exit_date": exit_, "gross_return_pct": gross,
                        "residual_return_pct": residual, "costs_pct": 0.2, "market_return_pct": 0.4, "beta": 1.1},
            "reward": {"schema_version": plf.POLICY["reward_version"], "reward": round((residual - 0.2) / scale, 6),
                       "risk_scale": {"risk_scale_pct": scale}}}}})


def counts(pg):  # noqa: F811
    with pg() as conn:
        return {t: conn.execute(f"SELECT count(*), md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {t} t"
                                ).fetchone() for t in ("agent_trade_intents", "agent_paper_positions",
                                                       "agent_paper_fills", "agent_trading_settings")}


def test_registers_once_snapshots_scores_and_never_writes_the_ledger(ledger):
    first = run(night_after("2026-11-05"))
    assert first["status"] == "OK" and first["protocol_sha256"] == plf.PROTOCOL_SHA256
    with ledger() as conn:
        pid, did = open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z")
        open_position(conn, "MSFT", "2026-11-13", "2026-11-05T21:05:00Z")                 # before registration
        open_position(conn, "NVDA", "2026-11-13", "2026-11-12T21:05:00Z", selector="source-report-classification")
        conn.commit()
    before = counts(ledger)
    night = nightly("2026-11-06", "2026-11-13")
    assert night["snapshot"]["in_scope"] == 1 and night["coverage"]["missed"] == []
    assert night["snapshot"]["out_of_scope"]["PRE_REGISTRATION"] == 1
    assert night["snapshot"]["out_of_scope"]["OTHER_SELECTOR"] == 1
    assert night["status"] == "ATTENTION" and any(          # a legacy pick after registration, listed
        s.startswith("SCOPE_EXCLUDED_AFTER_REGISTRATION") for s in night["sticky_attention"])
    assert counts(ledger) == before                                              # read-only toward the ledger
    nightly("2026-11-16", "2026-12-11")
    with ledger() as conn:
        close(conn, pid, "2026-12-14", 2.0)
        label(conn, did, "2026-11-13", "2026-12-14", 2.0, 1.4)
        conn.commit()
    done = run(night_after("2026-12-14"))
    assert done["outcomes"]["scored"] == 1 and done["status"] == "OK"           # the legacy note has expired
    with ledger() as conn:
        [result] = plf.outcomes(conn).values()
        assert result["status"] == "SCORED" and result["reconciliation"] == "MATCHED"
        assert result["score"] == pytest.approx((1.4 - 0.2) / 3.0)
        assert fvf.decisions(conn) == [] and sf.decisions(conn) == []           # the other trackers see nothing
    again = run(night_after("2026-12-15"))
    with ledger() as conn:
        assert again["outcomes"]["scored"] == 0 and len(plf.outcomes(conn)) == 1  # written once
        view = plf.summary(conn, now=at(2026, 12, 16, 12, 0))
    assert view["protocol_registered"] and not view["registered_late"] and view["positions_seen"] == 1
    assert view["scored_positions"] == 1 and view["evaluation"] == {"status": "PENDING", "final": False}
    assert view["paper_mode_share_so_far"] == 1.0 and view["current_mode"] == "PAPER"
    assert view["outcomes_by_status"] == {"SCORED:MATCHED": 1} and view["superseded_protocols"] == []
    assert "monthly_mean" not in json.dumps(view) and "lower" not in view       # coverage only, no estimate


def test_a_ledger_row_changed_after_its_snapshot_is_flagged_and_scored_from_the_copy(ledger):
    run(night_after("2026-11-05"))
    with ledger() as conn:
        pid, did = open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z")
        conn.commit()
    nightly("2026-11-06", "2026-11-13")
    with ledger() as conn:
        conn.execute("UPDATE agent_paper_positions SET entry_price=90 WHERE position_id=%s", (pid,))
        conn.commit()
    flagged = run(night_after("2026-11-16"))
    assert flagged["status"] == "ATTENTION" and flagged["snapshot"]["mutated"] == [f"{pid}:entry_price"]
    assert f"LEDGER_MUTATED:{pid}:entry_price:2026-11-16" in flagged["sticky_attention"]
    quiet = run(night_after("2026-11-17"))
    assert quiet["snapshot"]["mutated"] == [] and quiet["status"] == "ATTENTION"   # written once, still listed
    with ledger() as conn:
        assert plf.snapshots(conn, "entry")[pid]["payload"]["entry_price"] == 100.0
        conn.execute("DELETE FROM agent_paper_positions WHERE position_id=%s", (pid,))
        conn.commit()
    gone = run(night_after("2026-11-18"))
    assert gone["snapshot"]["mutated"] == [f"{pid}:ROW_MISSING"]


def test_a_late_first_snapshot_missed_nights_and_a_policy_change_are_recorded_not_fatal(ledger, monkeypatch):
    run(night_after("2026-11-05"))
    nightly("2026-11-06", "2026-11-12")
    with ledger() as conn:
        pid, _ = open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z")
        conn.commit()
    from stock_machine.agent_intelligence import bandit
    monkeypatch.setattr(bandit, "VERSION", "pooled-linear-confident-sector.v4")
    late = run(night_after("2026-11-18"))                                      # nights 11-13..11-17 skipped
    assert late["snapshot"]["late"] == [pid] and late["snapshot"]["new_entries"] == 1
    assert late["policy_drift"] == {"bandit_version": "pooled-linear-confident-sector.v4"}
    assert late["coverage"]["missed"] == ["2026-11-13", "2026-11-16", "2026-11-17"]
    assert any(s.startswith("POLICY_VERSION_CHANGED:bandit_version") for s in late["sticky_attention"])
    assert any(s.startswith(f"SNAPSHOT_LATE:{pid}") for s in late["sticky_attention"])
    nightly("2026-11-19", "2026-11-30")
    quiet = run(night_after("2026-12-01"))                                     # same drift, two weeks later
    assert quiet["policy_drift"] and quiet["status"] == "OK" and quiet["sticky_attention"] == []
    with ledger() as conn:
        view = plf.summary(conn, now=at(2026, 12, 2, 12, 0))
    checks = view["recorded_checks"]
    assert checks["POLICY_VERSION_CHANGED"] == 1 and checks["SNAPSHOT_LATE"] == 1 and checks["NIGHT_MISSED"] == 3


def test_a_protocol_change_refuses_and_missing_ledger_tables_are_tolerated(ledger, monkeypatch):
    with ledger() as conn:
        conn.execute("DROP TABLE agent_paper_marks, agent_paper_fills, agent_paper_positions, agent_trade_intents")
        conn.commit()
    empty = run(night_after("2026-11-05"))
    assert empty["status"] == "OK" and empty["snapshot"]["ledger_positions"] == 0
    monkeypatch.setattr(plf, "PROTOCOL", {**plf.PROTOCOL, "unit": "changed"})
    refused = run(night_after("2026-11-06"))
    assert refused["status"] == "PROTOCOL_CHANGED" and refused["reason_code"] == "FORWARD_PROTOCOL_CHANGED"


def test_a_new_selector_version_stays_in_scope_and_an_intent_edit_cannot_hide_an_exit(ledger):
    run(night_after("2026-11-05"))
    with ledger() as conn:
        open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z", selector="agent-intelligence.v3")
        pid, did = open_position(conn, "MSFT", "2026-11-13", "2026-11-12T21:05:00Z")
        conn.commit()
    night = nightly("2026-11-06", "2026-11-13")
    assert night["snapshot"]["in_scope"] == 2
    with ledger() as conn:
        assert plf.recorded_checks(conn)["POLICY_VERSION_CHANGED"] == 1          # the v3 selector, once
        conn.execute("""UPDATE agent_trade_intents SET risk_snapshot = risk_snapshot - 'holding_commitment'
                        WHERE intent_id=(SELECT source_intent_id FROM agent_paper_positions WHERE position_id=%s)""",
                     (pid,))
        close(conn, pid, "2026-12-14", -3.0)
        label(conn, did, "2026-11-13", "2026-12-14", -3.0, -3.4)
        conn.commit()
    after = run(night_after("2026-11-16"))
    assert f"{pid}:has_commitment" in after["snapshot"]["mutated"] and after["snapshot"]["new_exits"] == 1
    nightly("2026-11-17", "2026-12-14")
    with ledger() as conn:
        result = plf.outcomes(conn)[pid]
    assert result["status"] == "SCORED" and result["score"] == pytest.approx((-3.4 - 0.2) / 3.0)   # still counted


def test_a_capture_pause_counts_against_paper_and_a_labelled_position_is_scored_unsettled(ledger):
    run(night_after("2026-11-05"))
    with ledger() as conn:
        pid, did = open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z")
        conn.execute("""INSERT INTO operator_audit(actor,event,details,occurred_at) VALUES
            ('owner','CAPTURE_PAUSE_CHANGED','{"before":false,"after":true}','2026-12-01T12:00:00Z')""")
        conn.commit()
    nightly("2026-11-06", "2026-12-11")
    with ledger() as conn:
        label(conn, did, "2026-11-13", "2026-12-14", 2.0, 1.4)                  # labels keep coming; no exit
        conn.commit()
    waiting = nightly("2026-12-14", "2026-12-28")                              # due + 9 sessions
    assert waiting["outcomes"]["waiting"] == 1
    done = run(night_after("2026-12-29"))                                      # due + 10 sessions
    with ledger() as conn:
        result = plf.outcomes(conn)[pid]
        share = plf.summary(conn, now=at(2026, 12, 30, 12, 0))["paper_mode_share_so_far"]
    assert done["outcomes"]["scored"] == 1 and result["reconciliation"] == "UNSETTLED"
    assert share < 1.0                                                          # paused sessions are not PAPER


def test_the_final_is_written_once_after_every_window_position_resolves(ledger, monkeypatch):
    monkeypatch.setattr(plf, "EVALUATION_MONTHS", ("2026-11", "2026-11"))
    monkeypatch.setattr(plf, "EXTENDED_MONTHS", ("2026-11", "2026-12"))
    monkeypatch.setattr(plf, "MIN_POSITIONS", 2)
    monkeypatch.setattr(plf, "MIN_ACTIVE_MONTHS", 1)
    monkeypatch.setattr(plf, "behavior_digest", lambda: plf.EVALUATION_BEHAVIOR_SHA256)
    run(night_after("2026-11-05"))
    with ledger() as conn:
        a, da = open_position(conn, "AAPL", "2026-11-13", "2026-11-12T21:05:00Z")
        conn.commit()
    nightly("2026-11-06", "2026-12-11")
    with ledger() as conn:
        close(conn, a, "2026-12-14", 2.0)
        label(conn, da, "2026-11-13", "2026-12-14", 2.0, 1.4)
        b, db_ = open_position(conn, "MSFT", "2026-12-15", "2026-12-14T21:05:00Z")
        conn.commit()
    nightly("2026-12-14", "2026-12-15")
    with ledger() as conn:
        assert plf.extended(conn) and plf.final_evaluation(conn) is None       # one scored: short of 2, extend
    nightly("2026-12-16", "2027-01-13")
    with ledger() as conn:
        close(conn, b, "2027-01-14", 1.0)
        label(conn, db_, "2026-12-15", "2027-01-14", 1.0, 0.9)
        conn.commit()
    final = nightly("2027-01-14", "2027-01-15")
    assert final["evaluation_final"]
    with ledger() as conn:
        record = plf.final_evaluation(conn)
        assert record["window"] == ["2026-11", "2026-12"] and record["extended"] is True
        assert record["scored_positions"] == 2 and record["status"] == "EVALUATED"
        assert record["protocol_sha256"] == plf.PROTOCOL_SHA256 and record["integrity"]["LEDGER_MUTATED"] == []
    later = run(night_after("2027-01-19"))
    with ledger() as conn:
        assert plf.final_evaluation(conn) == record and later["evaluation_final"]   # never rewritten
        assert plf.summary(conn, now=at(2027, 1, 20, 12, 0))["evaluation"]["final"] is True


def test_a_protocol_registered_after_the_deadline_never_gets_a_verdict(ledger, monkeypatch):
    monkeypatch.setattr(plf, "EVALUATION_MONTHS", ("2026-11", "2026-11"))
    monkeypatch.setattr(plf, "behavior_digest", lambda: plf.EVALUATION_BEHAVIOR_SHA256)
    run(night_after("2026-11-10"))                                            # after the 2026-11-10 open
    with ledger() as conn:
        assert plf.summary(conn, now=at(2026, 11, 11, 12, 0))["registered_late"] is True
    run(night_after("2026-12-01"))
    with ledger() as conn:
        assert plf.final_evaluation(conn)["status"] == "REGISTERED_LATE"
