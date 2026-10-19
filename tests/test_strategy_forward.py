"""Strategy forward shadow: point-in-time flags, replay-identical trades, one evaluation."""

import math

import pytest

from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import strategy_forward as f
from stock_machine.agent_intelligence import strategy_signals as ss
from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import event_replay as er
from stock_machine.market_calendar import session_dates, session_offset

PINNED_PROTOCOL_SHA256 = "5672df5f4c9f11f9c90813eeb60afcdb94886737bed285cc202ffec6ea5b8517"
DAYS = session_dates("2025-01-02", "2027-06-30")


def rows_for(drift, wobble, start, *, gap_on=None, gap=0.0, spike=1.0, days=DAYS):
    rows, price = [], start
    for i, day in enumerate(days):
        price *= 1 + drift + wobble * math.sin(i / 4) + (gap if day == gap_on else 0.0)
        rows.append({"date": day, "open": price * 0.995, "high": price * 1.01, "low": price * 0.985, "close": price,
                     "adj_close": price, "volume": 1_000_000 * (spike if day == gap_on else 1.0)})
    return rows


SPY = rows_for(0.0004, 0.004, 400.0)
BREAKOUT = rows_for(0.001, 0.004, 40.0, gap_on="2026-10-22", gap=0.06, spike=3.0)


def upto(rows, session):
    return [r for r in rows if r["date"] <= session]


def decide(session, rows=BREAKOUT, events=None, eligible=frozenset({"VZ"}), missing=()):
    return f.decide_session(session, prices={"VZ": upto(rows, session)}, spy=upto(SPY, session),
                            events={"VZ": []} if events is None else events, s4_eligible=set(eligible),
                            missing=list(missing))


def test_the_protocol_is_registered_against_the_replay():
    assert f.PROTOCOL_SHA256 == digest(f.PROTOCOL) == PINNED_PROTOCOL_SHA256
    assert f.REPLAY_PROTOCOL_SHA256 == er.PROTOCOL_SHA256
    assert f.PROTOCOL["strategies"] == er.PROTOCOL["strategies"]
    assert f.FORWARD_ID != fvf.FORWARD_ID and f.DECISION_KIND == fvf.DECISION_KIND   # shared kinds, own id
    assert f.STRATEGIES == (*er.CONFIRMATORY, *er.EXPLORATORY)


def test_calendar():
    assert f.decision_sessions_through("2026-10-20") == []
    assert f.decision_sessions_through("2026-10-23") == ["2026-10-21", "2026-10-22", "2026-10-23"]
    assert f.prior_month_end("2026-10-30") == "2026-09-30" and f.prior_month_end("2026-11-02") == "2026-10-30"
    assert f.deadline_for("2026-10-23").isoformat() == "2026-10-26T13:30:00+00:00"     # Friday -> Monday open
    assert f.evaluation_end() == "2028-10-31"


def test_flags_use_only_the_sessions_close_and_s4_needs_eligibility():
    assert decide("2026-10-21")["rows"] == []
    row, = decide("2026-10-22")["rows"]
    assert row["flags"] == {"S1_volume_breakout": True, "S2_trend_pullback": False, "S3_post_earnings": False,
                            "S4_value_momentum": True}
    assert row["features"]["beta_63_vs_spy"] is not None and row["atr_ratio"] > 0
    assert decide("2026-10-22", eligible=set())["rows"][0]["flags"]["S4_value_momentum"] is False
    # Later prices cannot change a session's flags.
    altered = upto(BREAKOUT, "2026-10-22") + [dict(r, close=r["close"] * 0.5, adj_close=r["adj_close"] * 0.5)
                                              for r in BREAKOUT if r["date"] > "2026-10-22"]
    assert decide("2026-10-22", rows=altered)["rows"] == decide("2026-10-22")["rows"]


def test_a_stock_without_a_bar_or_filings_has_no_decision():
    out = decide("2026-10-22", missing=["VZ"])
    assert out["with_bar"] == [] and out["missing"] == ["VZ"] and out["rows"] == []
    row, = decide("2026-10-22", events={})["rows"]
    assert row["flags"]["S3_post_earnings"] is None                  # filings not read: no S3 decision


EARNINGS = rows_for(0.0005, 0.004, 40.0)
for r in EARNINGS:
    if r["date"] >= "2026-11-03":
        for k in ("open", "high", "low", "close", "adj_close"):
            r[k] *= 1.10
ANNOUNCED = {"VZ": [{"accession": "0000732712-26-000041", "acceptance_utc": "2026-11-02T21:05:00+00:00",
                     "items": "2.02,9.01", "reaction_session": "2026-11-03"}]}


def test_post_earnings_signal_on_the_second_reaction_session_with_frozen_levels():
    assert decide("2026-11-03", rows=EARNINGS, events=ANNOUNCED, eligible=())["rows"] == []
    row, = decide("2026-11-04", rows=EARNINGS, events=ANNOUNCED, eligible=())["rows"]
    assert row["flags"]["S3_post_earnings"] and not row["flags"]["S1_volume_breakout"]
    assert row["s3"]["announcements"][0]["accession"] == "0000732712-26-000041"
    sessions = session_dates(session_offset("2026-11-04", -f.HISTORY_SESSIONS), "2026-11-04")
    bars = ss.Bars(upto(EARNINGS, "2026-11-04"), "2026-11-04", sessions)
    i = bars.index["2026-11-04"]
    assert row["s3"]["gap_ratio"] == pytest.approx(bars.close[i - 2] / bars.close[i])
    assert row["s3"]["atr_ratio"] == pytest.approx(bars.atr[i - 2] / bars.close[i])
    assert row["s3"]["reaction_z"] >= ss.RULES["S3_post_earnings"]["z_min"]
    small = [dict(r) for r in rows_for(0.0005, 0.004, 40.0)]
    quiet, = decide("2026-11-04", rows=small, events=ANNOUNCED, eligible=())["rows"]
    assert quiet["flags"]["S3_post_earnings"] is False and quiet["s3"]["reaction_z"] < 2   # recorded, not traded


def decision(session, rows, *, record_id=None, created="2026-01-01T00:00:00+00:00", recorded=None):
    return {"request_key": f"{f.FORWARD_ID}:{session}", "record_id": record_id or "evidence_" + session,
            "content_hash": "h" + session, "recorded_at": recorded or created,
            "payload": {"decision_session": session, "freeze_deadline": f.deadline_for(session).isoformat(),
                        "created_at": created, "rows": rows, "with_bar": ["VZ"]}}


def chain_for(rows_source, sessions_with_signal, strategy="S1_volume_breakout"):
    out = []
    for s in sessions_with_signal:
        row, = [r for r in decide(s, rows=rows_source)["rows"] if r["flags"][strategy]]
        out.append(decision(s, [row], created=f"{s}T22:00:00+00:00"))
    return out


def resolve(rows_source, decisions, completed, recorded=None, strategy="S1_volume_breakout"):
    chain = f.signals_by_chain(decisions)[("VZ", strategy)]
    sessions = session_dates(session_offset(chain[0][0], -f.HISTORY_SESSIONS), completed)
    bars = ss.Bars(upto(rows_source, completed), completed, sessions)
    return f.resolve_chain("VZ", strategy, chain, bars=bars, stock=dr._adjusted(upto(rows_source, completed)),
                           spy=dr._adjusted(upto(SPY, completed)), completed=completed, recorded=recorded or {})


def test_a_trade_is_recorded_only_after_its_window_with_the_replay_reward():
    decisions = chain_for(BREAKOUT, ["2026-10-22"])
    last = session_offset("2026-10-23", 20)
    new, state = resolve(BREAKOUT, decisions, session_offset(last, -1))
    assert new == [] and state["state"] == "OPEN" and state["window_ends"] == last
    (key, out), = resolve(BREAKOUT, decisions, last)[0]
    assert key == f.outcome_key("S1_volume_breakout", "VZ", "2026-10-22") and out["status"] == "SCORED"
    sessions = session_dates(session_offset("2026-10-22", -f.HISTORY_SESSIONS), last)
    bars = ss.Bars(upto(BREAKOUT, last), last, sessions)
    i = bars.index["2026-10-22"]
    exit_i, reason = ss.simulate_exit(bars, i + 1, "S1_volume_breakout", atr_ref=bars.atr[i])
    assert (out["exit"], out["reason"]) == (bars.sessions[exit_i], reason)
    row = decisions[0]["payload"]["rows"][0]
    expected = dr.rewards_between("2026-10-23", out["exit"], {"features": row["features"]},
                                  stock=dr._adjusted(BREAKOUT), spy=dr._adjusted(SPY), horizon=20)
    assert out["reward"] == pytest.approx(expected["rewards"]["LONG"]) and out["free_from"] == out["exit"]


def test_exit_levels_survive_a_later_dividend_adjustment():
    decisions = chain_for(BREAKOUT, ["2026-10-22"])
    last = session_offset("2026-10-23", 20)
    adjusted = [dict(r, adj_close=r["adj_close"] * 0.97) if r["date"] < "2026-11-05" else r for r in BREAKOUT]
    (_, plain), = resolve(BREAKOUT, decisions, last)[0]
    (_, rescaled), = resolve(adjusted, decisions, last)[0]
    assert (plain["exit"], plain["reason"]) == (rescaled["exit"], rescaled["reason"])


def test_one_open_trade_per_stock_and_strategy_and_recorded_trades_are_final():
    decisions = chain_for(BREAKOUT, ["2026-10-22"])
    inside = dict(decisions[0], payload={**decisions[0]["payload"], "decision_session": "2026-10-27",
                                         "freeze_deadline": f.deadline_for("2026-10-27").isoformat(),
                                         "created_at": "2026-10-27T22:00:00+00:00"},
                  request_key=f"{f.FORWARD_ID}:2026-10-27", record_id="evidence_2026-10-27",
                  recorded_at="2026-10-27T22:00:00+00:00")
    completed = session_offset("2026-10-23", 25)
    new, state = resolve(BREAKOUT, decisions + [inside], completed)
    assert new[0][1]["exit"] == "2026-11-20"                  # held to its maximum
    assert [o["signal"] for _, o in new] == ["2026-10-22"] and state == {"state": "CLEAR"}   # 10-27 skipped
    # A recorded trade is reused as stored, never recomputed.
    stored = {new[0][0]: {**new[0][1], "free_from": session_offset("2026-10-22", 40)}}
    again, _ = resolve(BREAKOUT, decisions + [inside], completed, recorded=stored)
    assert again == []


def test_a_missing_bar_blocks_then_excludes_and_locks_like_the_replay():
    decisions = chain_for(BREAKOUT, ["2026-10-22"])
    gap = session_offset("2026-10-23", 15)         # retried until 10 sessions after the gap, past the window
    gappy = [r for r in BREAKOUT if r["date"] != gap]
    last = session_offset("2026-10-23", 20)
    new, state = resolve(gappy, decisions, last)
    assert new == [] and state == {"state": "BLOCKED", "signal": "2026-10-22", "first_missing": gap}
    (_, out), = resolve(gappy, decisions, session_offset(gap, f.RETRY_SESSIONS))[0]
    assert out["status"] == "EXCLUDED_MISSING_BARS" and out["free_from"] == session_offset("2026-10-22", 21)


def test_a_decision_written_after_its_deadline_is_never_traded():
    late = chain_for(BREAKOUT, ["2026-10-22"])[0]
    late["recorded_at"] = "2026-10-23T13:31:00+00:00"
    assert f.signals_by_chain([late]) == {}


def outcome(strategy, signal, reward, status="SCORED"):
    return {"strategy": strategy, "signal": signal, "status": status, "reward": reward}


def test_evaluation_book_coverage_and_family_quantile():
    months = [f"{y}-{m:02d}" for y in (2026, 2027, 2028) for m in range(1, 13)
              if "2026-11" <= f"{y}-{m:02d}" <= "2028-10"]
    denominators = {m: 10 for m in months}
    trades = [outcome("S1_volume_breakout", f"{m}-15", 0.2 + 0.1 * (k % 3)) for k, m in enumerate(months)
              for _ in range(3)]
    trades += [outcome("S2_trend_pullback", f"{m}-15", -0.1) for m in months[:5]]
    trades += [outcome("S1_volume_breakout", "2026-10-28", 9.0)]          # before the window: ignored
    result = f.evaluate(trades, {**denominators, "2026-10": 10})
    s1, s2 = result["strategies"]["S1_volume_breakout"], result["strategies"]["S2_trend_pullback"]
    assert s1["months"] == 24 and s1["trades"] == 72 and s1["verdict"] == "PASS_REQUIRES_INDEPENDENT_REVIEW"
    values = er.book([t for t in trades if t["strategy"] == "S1_volume_breakout" and t["signal"] >= "2026-11"],
                     denominators, months)
    assert s1["lower"] == pytest.approx(er.interval(values, er.Z_FAMILY)["lower"])
    assert s2["verdict"] == "INSUFFICIENT_COVERAGE" and s2["trades"] == 5
    assert result["strategies"]["S4_value_momentum"]["role"] == "exploratory"


def test_denominators_use_the_last_valid_session_of_each_month():
    a = decision("2026-10-29", [], created="2026-10-29T22:00:00+00:00")
    b = decision("2026-10-30", [], created="2026-10-30T22:00:00+00:00")
    b["payload"]["with_bar"] = ["VZ", "T"]
    late = decision("2026-11-02", [], created="2026-11-03T15:00:00+00:00")
    assert f.denominators([a, b, late]) == {"2026-10": 2}


def test_open_trades_lists_the_unresolved_trade_per_chain():
    decisions = chain_for(BREAKOUT, ["2026-10-22"])
    assert f.open_trades(decisions, {}) == [{"ticker": "VZ", "strategy": "S1_volume_breakout",
                                              "signal": "2026-10-22", "entry": "2026-10-23",
                                              "window_ends": session_offset("2026-10-23", 20)}] + [
        {"ticker": "VZ", "strategy": "S4_value_momentum", "signal": "2026-10-22", "entry": "2026-10-23",
         "window_ends": session_offset("2026-10-23", 30)}]


class FakeSEC:
    def __init__(self, payloads, failures=0):
        self.payloads, self.failures, self.urls = payloads, failures, []

    def get(self, url):
        self.urls.append(url)
        cik = url.rsplit("CIK", 1)[1].split(".")[0]
        if self.failures:
            self.failures -= 1
            return type("R", (), {"raise_for_status": lambda s: (_ for _ in ()).throw(RuntimeError("503"))})()
        payload = self.payloads[cik]
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: payload})()


def test_sec_events_read_recent_item_202_filings_with_retries(monkeypatch):
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    recent = {"filings": {"recent": {
        "accessionNumber": ["a2", "a1", "old"], "form": ["8-K", "8-K", "8-K"],
        "filingDate": ["2026-11-02", "2026-10-01", "2026-07-30"],
        "acceptanceDateTime": ["2026-11-02T21:05:00.000Z", "2026-10-01T12:00:00.000Z", "2026-07-30T20:00:00.000Z"],
        "items": ["2.02,9.01", "8.01", "2.02"]}}}
    client = FakeSEC({"0000732712": recent}, failures=1)
    events, failed = f.sec_events({"VZ": "732712", "T": None}, "2026-11-04", client=client)
    assert failed == ["T"] and len(client.urls) == 2                       # one retry after a 503
    assert client.urls[0] == "https://data.sec.gov/submissions/CIK0000732712.json"
    assert [(e["accession"], e["reaction_session"]) for e in events["VZ"]] == [("a2", "2026-11-03")]
    gone = FakeSEC({}, failures=3)
    assert f.sec_events({"VZ": "732712"}, "2026-11-04", client=gone) == ({}, ["VZ"])


def _crons():
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/strategy-forward.yml").read_text()
    assert "if: github.ref == 'refs/heads/main'" in text
    return [(int(h), int(m), days) for m, h, days in re.findall(r'cron: "(\d+) (\d+) \* \* ([\d-]+)"', text)]


@pytest.mark.parametrize("session", ["2026-10-30", "2027-03-05", "2027-03-15", "2027-11-05"])
def test_every_morning_run_is_a_final_attempt_in_both_seasons(session):
    from datetime import timedelta
    opening = f.deadline_for(session)
    morning = [(h, m) for h, m, days in _crons() if days == "1-5"]
    assert len(morning) >= 3
    for h, m in morning:
        run_at = opening.replace(hour=h, minute=m)
        assert timedelta(0) < opening - run_at <= timedelta(hours=f.FINAL_ATTEMPT_HOURS), (session, h, m)


def test_only_main_runs_and_nothing_is_read_first(monkeypatch):
    from datetime import datetime, timezone

    from stock_machine import db

    def no_db():
        raise AssertionError("must refuse before touching the database")
    monkeypatch.setattr(db, "connect", no_db)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    out = f.run_daily(now=datetime(2026, 10, 22, 4, 20, tzinfo=timezone.utc))
    assert out["status"] == "REFUSED_NON_MAIN" and out["reason_code"] == "UNSET"


def test_the_runner_exits_nonzero_unless_the_freeze_is_ok():
    from scripts.run_strategy_forward import exit_code
    assert exit_code({"status": "OK", "freeze": {"status": "FROZEN"}}) == 0
    assert exit_code({"status": "OK", "freeze": {"status": "FREEZE_PENDING_DATA"}}) == 1
    assert exit_code({"status": "ATTENTION", "freeze": {"status": "FROZEN"}}) == 1
