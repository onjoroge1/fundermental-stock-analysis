"""Strategy forward shadow: point-in-time flags, replay-identical trades, one evaluation."""

import math
import random

import pytest

from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import strategy_forward as f
from stock_machine.agent_intelligence import strategy_signals as ss
from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import event_replay as er
from stock_machine.market_calendar import session_dates, session_offset

PINNED_PROTOCOL_SHA256 = "0a26e0b737ad8b1452d7f182b17e51c225ca2ab544143953738b1d23dbd19ee5"
PINNED_SHADOW_BEHAVIOR = "7a03fc75b779934e6d48394d0d709c08d2b977058c1e9a024d220cc4942b1a3a"
DAYS = session_dates("2025-01-02", "2027-06-30")
SIGNAL = "2026-10-27"


def rows_for(drift, wobble, start, *, gap_on=None, gap=0.0, spike=1.0, days=DAYS):
    rows, price = [], start
    for i, day in enumerate(days):
        price *= 1 + drift + wobble * math.sin(i / 4) + (gap if day == gap_on else 0.0)
        rows.append({"date": day, "open": price * 0.995, "high": price * 1.01, "low": price * 0.985, "close": price,
                     "adj_close": price, "volume": 1_000_000 * (spike if day == gap_on else 1.0)})
    return rows


SPY = rows_for(0.0004, 0.004, 400.0)
BREAKOUT = rows_for(0.001, 0.004, 40.0, gap_on=SIGNAL, gap=0.06, spike=3.0)


def upto(rows, session):
    return [r for r in rows if r["date"] <= session]


def decide(session, rows=BREAKOUT, events=None, eligible=frozenset({"VZ"}), missing=()):
    return f.decide_session(session, prices={"VZ": rows}, spy=upto(SPY, session),
                            events={"VZ": []} if events is None else events, s4_eligible=set(eligible),
                            missing=list(missing))


def test_the_protocol_is_registered_against_the_replay():
    assert f.PROTOCOL_SHA256 == digest(f.PROTOCOL) == PINNED_PROTOCOL_SHA256
    assert f.REPLAY_PROTOCOL_SHA256 == er.PROTOCOL_SHA256
    assert f.PROTOCOL["strategies"] == er.PROTOCOL["strategies"]
    assert f.FORWARD_ID != fvf.FORWARD_ID and f.DECISION_KIND == fvf.DECISION_KIND   # shared kinds, own id
    assert f.STRATEGIES == (*er.CONFIRMATORY, *er.EXPLORATORY)
    assert "agent_intelligence/strategy_forward.py" in f.DECISION_SOURCES
    assert "DECISION_SOURCES_CHANGED" not in f.LOSS_STATUSES          # reported once, not sticky


def test_the_shadows_own_flag_and_trade_code_is_pinned():
    assert f.behavior_digest() == f.SHADOW_BEHAVIOR_SHA256 == PINNED_SHADOW_BEHAVIOR
    assert f.PROTOCOL["shadow_behavior_sha256"] == PINNED_SHADOW_BEHAVIOR


def test_calendar():
    assert f.decision_sessions_through("2026-10-23") == []
    assert f.decision_sessions_through("2026-10-28") == ["2026-10-26", "2026-10-27", "2026-10-28"]
    assert f.prior_month_end("2026-10-30") == "2026-09-30" and f.prior_month_end("2026-11-02") == "2026-10-30"
    assert f.deadline_for("2026-10-30").isoformat() == "2026-11-02T14:30:00+00:00"     # Friday -> Monday, EST
    assert f.evaluation_end() == "2028-10-31"


def test_flags_use_only_the_sessions_close_and_s4_needs_eligibility():
    assert decide("2026-10-26")["rows"] == []
    row, = decide(SIGNAL)["rows"]
    assert row["flags"] == {"S1_volume_breakout": True, "S2_trend_pullback": False, "S3_post_earnings": False,
                            "S4_value_momentum": True}
    assert row["features"]["beta_63_vs_spy"] is not None and row["atr_ratio"] > 0
    assert decide(SIGNAL, eligible=set())["rows"][0]["flags"]["S4_value_momentum"] is False
    # decide_session receives the full history; prices after the session cannot change its flags.
    altered = upto(BREAKOUT, SIGNAL) + [dict(r, close=r["close"] * 0.5, adj_close=r["adj_close"] * 0.5, volume=9e9)
                                        for r in BREAKOUT if r["date"] > SIGNAL]
    assert decide(SIGNAL, rows=altered) == decide(SIGNAL) == decide(SIGNAL, rows=upto(BREAKOUT, SIGNAL))


def test_a_stock_without_a_complete_bar_or_filings_has_no_decision():
    out = decide(SIGNAL, missing=["VZ"])
    assert out["with_bar"] == out["with_window"] == [] and out["missing"] == ["VZ"] and out["rows"] == []
    broken = [dict(r, volume=None) if r["date"] == SIGNAL else r for r in BREAKOUT]
    assert decide(SIGNAL, rows=broken)["missing"] == ["VZ"]          # stored but incomplete
    row, = decide(SIGNAL, events={})["rows"]
    assert row["flags"]["S3_post_earnings"] is None                  # filings not read: no S3 decision
    assert decide(SIGNAL)["with_window"] == ["VZ"]


EARNINGS = rows_for(0.0005, 0.004, 40.0)
for _r in EARNINGS:
    if _r["date"] >= "2026-11-03":
        for _k in ("open", "high", "low", "close", "adj_close"):
            _r[_k] *= 1.10
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
    quiet, = decide("2026-11-04", rows=rows_for(0.0005, 0.004, 40.0), events=ANNOUNCED, eligible=())["rows"]
    assert quiet["flags"]["S3_post_earnings"] is False and quiet["s3"]["reaction_z"] < 2   # recorded, not traded


def decision(session, rows, *, created=None, recorded=None, with_window=("VZ",)):
    created = created or f"{session}T22:00:00+00:00"
    return {"request_key": f"{f.FORWARD_ID}:{session}", "record_id": "evidence_" + session,
            "content_hash": "h" + session, "recorded_at": recorded or created,
            "payload": {"decision_session": session, "freeze_deadline": f.deadline_for(session).isoformat(),
                        "created_at": created, "rows": rows, "with_bar": list(with_window),
                        "with_window": list(with_window)}}


def chain_for(rows_source, sessions_with_signal, strategy="S1_volume_breakout"):
    out = []
    for s in sessions_with_signal:
        row, = [r for r in decide(s, rows=rows_source)["rows"] if r["flags"][strategy]]
        out.append(decision(s, [row]))
    return out


def resolve(rows_source, decisions, completed, recorded=None, strategy="S1_volume_breakout"):
    chain = f.signals_by_chain(decisions)[("VZ", strategy)]
    sessions = session_dates(session_offset(chain[0][0], -f.HISTORY_SESSIONS), completed)
    bars = ss.Bars(upto(rows_source, completed), completed, sessions)
    return f.resolve_chain("VZ", strategy, chain, bars=bars, stock=dr._adjusted(upto(rows_source, completed)),
                           spy=dr._adjusted(upto(SPY, completed)), completed=completed, recorded=recorded or {})


ENTRY = session_offset(SIGNAL, 1)
LAST = session_offset(ENTRY, 20)


def test_a_trade_is_recorded_only_after_its_window_with_the_replay_reward():
    decisions = chain_for(BREAKOUT, [SIGNAL])
    new, state = resolve(BREAKOUT, decisions, session_offset(LAST, -1))
    assert new == [] and state["state"] == "OPEN" and state["window_ends"] == LAST
    (key, out), = resolve(BREAKOUT, decisions, LAST)[0]
    assert key == f.outcome_key("S1_volume_breakout", "VZ", SIGNAL) and out["status"] == "SCORED"
    bars = ss.Bars(upto(BREAKOUT, LAST), LAST, session_dates(session_offset(SIGNAL, -f.HISTORY_SESSIONS), LAST))
    i = bars.index[SIGNAL]
    exit_i, reason = ss.simulate_exit(bars, i + 1, "S1_volume_breakout", atr_ref=bars.atr[i])
    assert (out["exit"], out["reason"]) == (bars.sessions[exit_i], reason) == (LAST, "MAX_HOLD")
    row = decisions[0]["payload"]["rows"][0]
    expected = dr.rewards_between(ENTRY, out["exit"], {"features": row["features"]},
                                  stock=dr._adjusted(BREAKOUT), spy=dr._adjusted(SPY), horizon=20)
    assert out["reward"] == pytest.approx(expected["rewards"]["LONG"]) and out["free_from"] == out["exit"]


def stop_path(adjustment=1.0):
    """The breakout, then a fall of 2.1x the signal-day ATR from the peak on the third session."""
    row, = decide(SIGNAL)["rows"]
    close = row["close"]
    peak = close * 1.03
    after = {session_offset(SIGNAL, 1): close, session_offset(SIGNAL, 2): peak}
    after[session_offset(SIGNAL, 3)] = peak - 2.1 * row["atr_ratio"] * close
    rows, level = [], None
    for r in BREAKOUT:
        if r["date"] > SIGNAL:
            level = after.get(r["date"], level if level is not None else close)
            r = {**r, "open": level, "high": level * 1.002, "low": level * 0.998, "close": level, "adj_close": level}
        rows.append(dict(r, adj_close=r["adj_close"] * adjustment))
    return rows


def test_exit_levels_are_ratios_so_a_dividend_adjustment_cannot_move_a_binding_stop():
    plain, adjusted = stop_path(), stop_path(0.9)      # a later adjustment rescales the whole window
    decisions = chain_for(plain, [SIGNAL])
    (_, a), = resolve(plain, decisions, LAST)[0]
    (_, b), = resolve(adjusted, decisions, LAST)[0]
    assert a["reason"] == b["reason"] == "TRAILING_STOP" and a["exit"] == b["exit"] == session_offset(SIGNAL, 4)
    # Frozen absolute levels would not trigger on the adjusted history.
    bars = ss.Bars(upto(adjusted, LAST), LAST, session_dates(session_offset(SIGNAL, -f.HISTORY_SESSIONS), LAST))
    row = decisions[0]["payload"]["rows"][0]
    absolute = ss.simulate_exit(bars, bars.index[SIGNAL] + 1, "S1_volume_breakout",
                                atr_ref=row["atr_ratio"] * row["close"])
    assert absolute[1] != "TRAILING_STOP"


def later_signal(decisions, session):
    return decision(session, decisions[0]["payload"]["rows"])


def test_one_open_trade_per_stock_and_strategy_and_recorded_trades_are_final():
    decisions = chain_for(BREAKOUT, [SIGNAL])
    inside = later_signal(decisions, "2026-11-02")
    completed = session_offset("2026-11-03", 25)
    new, state = resolve(BREAKOUT, decisions + [inside], completed)
    assert new[0][1]["exit"] == LAST                                    # held to its maximum
    assert [o["signal"] for _, o in new] == [SIGNAL] and state == {"state": "CLEAR"}   # 11-02 skipped
    # A recorded trade is reused as stored: its stored free_from (not a recomputed exit) frees the stock.
    stored = {new[0][0]: {**new[0][1], "free_from": "2026-10-30"}}
    again, _ = resolve(BREAKOUT, decisions + [inside], completed, recorded=stored)
    assert [o["signal"] for _, o in again] == ["2026-11-02"]


def test_a_missing_bar_blocks_then_excludes_and_locks_like_the_replay():
    decisions = chain_for(BREAKOUT, [SIGNAL])
    gap = session_offset(ENTRY, 15)                # retried until 10 sessions after the gap, past the window
    gappy = [r for r in BREAKOUT if r["date"] != gap]
    new, state = resolve(gappy, decisions, LAST)
    assert new == [] and state == {"state": "BLOCKED", "signal": SIGNAL, "first_missing": gap}
    (_, out), = resolve(gappy, decisions, session_offset(gap, f.RETRY_SESSIONS))[0]
    assert out["status"] == "EXCLUDED_MISSING_BARS" and out["free_from"] == session_offset(SIGNAL, 21)


def test_a_decision_written_after_its_deadline_is_never_traded():
    late = chain_for(BREAKOUT, [SIGNAL])[0]
    late["recorded_at"] = f.deadline_for(SIGNAL).isoformat()
    assert f.signals_by_chain([late]) == {}


def test_trades_match_the_replay_on_random_paths_with_missing_bars():
    """Freeze nightly with decide_session, resolve with resolve_chain, and compare with event_replay.trades_for."""
    every = session_dates("2023-01-03", "2026-06-30")
    start, cutoff, completed = "2025-03-03", "2026-02-27", "2026-06-30"

    def series(seed, drift, vol, base, drop, jumps=()):
        rnd = random.Random(seed)
        rows, p = [], base
        for d in every:
            p *= 1 + drift + rnd.gauss(0, vol) + (0.06 if rnd.random() < 0.01 else 0) + (0.12 if d in jumps else 0)
            volume = 1_000_000 * (3.0 if rnd.random() < 0.05 else 1.0)
            if rnd.random() < drop and d > start:
                continue
            rows.append({"date": d, "open": p * 0.995, "high": p * (1.006 + abs(rnd.gauss(0, vol))),
                         "low": p * (0.99 - abs(rnd.gauss(0, vol))), "close": p, "adj_close": p, "volume": volume})
        return rows
    spy = series(1, 0.0004, 0.008, 400.0, 0.0)
    rnd = random.Random(11)
    reactions = {t: sorted(rnd.sample([d for d in every if start < d < cutoff], 12)) for t in ("X", "Y")}
    prices = {"X": series(5, 0.0015, 0.012, 50.0, 0.004, jumps=set(reactions["X"])),
              "Y": series(9, 0.0012, 0.010, 80.0, 0.004, jumps=set(reactions["Y"]))}
    have = {t: {r["date"] for r in rows} for t, rows in prices.items()}
    eligible_months = {d[:7] for d in every if rnd.random() < 0.5}
    decisions = []
    for s in session_dates(start, cutoff):
        r = session_offset(s, -1)
        events = {t: [{"accession": t + r, "acceptance_utc": r + "T12:00:00+00:00", "items": "2.02",
                       "reaction_session": r}] if r in reactions[t] else [] for t in prices}
        out = f.decide_session(s, prices=prices, spy=upto(spy, s), events=events,
                               s4_eligible=set(prices) if f.prior_month_end(s)[:7] in eligible_months else set(),
                               missing=[t for t in prices if s not in have[t]])
        decisions.append(decision(s, out["rows"], with_window=out["with_window"]))
    chains = f.signals_by_chain(decisions)
    sessions = session_dates("2015-06-01", completed)
    month_ends = sorted({max(s for s in sessions if s[:7] == m) for m in {s[:7] for s in sessions}})
    sbars = ss.Bars(spy, completed, sessions)
    statuses, compared = set(), 0
    for ticker, rows in prices.items():
        bars = ss.Bars(rows, completed, session_dates(session_offset(start, -f.HISTORY_SESSIONS), completed))
        rbars = ss.Bars(rows, completed, sessions)
        s3 = er.s3_entries(rbars, sbars, rows, spy, reactions[ticker])

        def s4(b, i):
            prior = [m for m in month_ends if m < b.sessions[i]]
            return prior[-1][:7] in eligible_months and (ss.s1_entry(b, i) or ss.s2_entry(b, i))
        rules = {"S1_volume_breakout": ss.s1_entry, "S2_trend_pullback": ss.s2_entry,
                 "S3_post_earnings": lambda b, i, s3=s3: s3.get(i, False), "S4_value_momentum": s4}
        for strategy, rule in rules.items():
            new, _ = f.resolve_chain(ticker, strategy, chains.get((ticker, strategy), []), bars=bars,
                                     stock=dr._adjusted(rows), spy=dr._adjusted(spy), completed=completed, recorded={})
            got = [(o["signal"], o["status"], o.get("exit"), o.get("reason"), round(o.get("reward") or 0, 9))
                   for _, o in new]
            replay = er.trades_for(ticker, rows=rows, spy_rows=spy, bars=rbars, spy_bars=sbars, strategy=strategy,
                                   entry=lambda b, i, rule=rule: rule(b, i) if b.sessions[i] >= start else False,
                                   stage_end=cutoff)
            expected = [(t["signal"], t["status"], t.get("exit"), t.get("reason"), round(t.get("reward") or 0, 9))
                        for t in replay]
            assert got == expected, (ticker, strategy)
            statuses |= {t[1] for t in expected}
            compared += len(expected)
    assert compared >= 25 and {"SCORED", "EXCLUDED_MISSING_BARS"} <= statuses


def outcome(strategy, signal, reward, status="SCORED"):
    return {"strategy": strategy, "signal": signal, "status": status, "reward": reward}


def test_evaluation_book_coverage_and_family_quantile(monkeypatch):
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
    monkeypatch.setattr(f, "EVALUATION_MONTHS", ("2026-10", "2026-10"))   # read at call time
    assert f.evaluate(trades, {**denominators, "2026-10": 10})["strategies"]["S1_volume_breakout"]["trades"] == 1


def test_denominators_use_the_replays_unit_at_the_last_valid_session_of_each_month():
    a = decision("2026-10-29", [])
    b = decision("2026-10-30", [], with_window=("VZ", "T"))
    b["payload"]["with_bar"] = ["VZ", "T", "NEW"]                         # a new listing without history
    late = decision("2026-11-02", [], created="2026-11-03T15:00:00+00:00")
    assert f.denominators([a, b, late]) == {"2026-10": 2}


def test_trades_in_progress_show_signalled_exits_and_the_next_trade():
    rows = stop_path()
    decisions = chain_for(rows, [SIGNAL]) + [later_signal(chain_for(rows, [SIGNAL]), "2026-11-04")]
    completed = "2026-11-06"
    bars = ss.Bars(upto(rows, completed), completed,
                   session_dates(session_offset(SIGNAL, -f.HISTORY_SESSIONS), completed))
    view = f.provisional_trades("VZ", f.signals_by_chain(decisions), bars=bars, completed=completed, recorded={})
    s1 = [v for v in view if v["strategy"] == "S1_volume_breakout"]
    assert s1[0]["status"] == "EXIT_SIGNALLED" and s1[0]["exit"] == session_offset(SIGNAL, 4)
    assert s1[1]["signal"] == "2026-11-04" and s1[1]["status"] == "HOLDING"
    assert f.unresolved_counts(decisions, {})["S1_volume_breakout"] == 1


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


RECENT = {"filings": {"recent": {
    "accessionNumber": ["a2", "a1", "old"], "form": ["8-K", "8-K", "8-K"],
    "filingDate": ["2026-11-02", "2026-10-01", "2026-07-30"],
    "acceptanceDateTime": ["2026-11-02T21:05:00.000Z", "2026-10-01T12:00:00.000Z", "2026-07-30T20:00:00.000Z"],
    "items": ["2.02,9.01", "8.01", "2.02"]}}}


def test_sec_events_read_recent_item_202_filings_with_a_retry(monkeypatch):
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    client = FakeSEC({"0000732712": RECENT}, failures=1)
    events, failed = f.sec_events({"VZ": "732712", "T": None}, "2026-11-04", client=client)
    assert failed == ["T"] and len(client.urls) == 2                       # one retry after a 503
    assert client.urls[0] == "https://data.sec.gov/submissions/CIK0000732712.json"
    assert [(e["accession"], e["reaction_session"]) for e in events["VZ"]] == [("a2", "2026-11-03")]


def test_sec_events_are_bounded_by_time_and_consecutive_failures(monkeypatch):
    monkeypatch.setattr(f.time, "sleep", lambda s: None)
    down = FakeSEC({}, failures=999)
    ciks = {f"T{i:02d}": str(1000 + i) for i in range(12)}
    events, failed = f.sec_events(ciks, "2026-11-04", client=down)
    assert events == {} and failed == sorted(ciks)
    assert len(down.urls) == 2 * f.SEC_MAX_CONSECUTIVE_FAILURES             # the rest are not requested
    none_left = FakeSEC({"0000732712": RECENT})
    assert f.sec_events({"VZ": "732712"}, "2026-11-04", client=none_left, budget_seconds=0) == ({}, ["VZ"])
    assert none_left.urls == []


def _crons():
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/strategy-forward.yml").read_text()
    assert "if: github.ref == 'refs/heads/main'" in text
    return [(int(h), int(m), days) for m, h, days in re.findall(r'cron: "(\d+) (\d+) \* \* ([\d-]+)"', text)]


@pytest.mark.parametrize("session", ["2026-10-26", "2026-10-30", "2027-03-05", "2027-03-15", "2027-11-05"])
def test_morning_runs_wait_for_data_then_at_least_two_final_attempts_before_the_open(session):
    from datetime import timedelta

    from stock_machine.market_calendar import latest_completed_session
    opening = f.deadline_for(session)
    morning = sorted((h, m) for h, m, days in _crons() if days == "1-5")
    lead = [opening - opening.replace(hour=h, minute=m) for h, m in morning]
    assert all(t > timedelta(0) for t in lead), session
    final = [t <= timedelta(hours=f.FINAL_ATTEMPT_HOURS) for t in lead]
    assert not final[0] and sum(final) >= 2 and final[-1], (session, lead)
    early = lead[:3]   # fallbacks for the night run: hours of GitHub delay still land before the open
    assert min(early) >= timedelta(hours=4, minutes=55) and not any(final[:3]), (session, lead)
    for h, m in morning:   # every morning run freezes this session, against this open
        assert latest_completed_session(opening.replace(hour=h, minute=m)) == session and (h, m) > (4, 20)
    assert [(h, m) for h, m, days in _crons() if days == "2-6"] == [(4, 20)]


def test_only_main_runs_and_nothing_is_read_first(monkeypatch):
    from datetime import datetime, timezone

    from stock_machine import db

    def no_db():
        raise AssertionError("must refuse before touching the database")
    monkeypatch.setattr(db, "connect", no_db)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    out = f.run_daily(now=datetime(2026, 10, 27, 4, 20, tzinfo=timezone.utc))
    assert out["status"] == "REFUSED_NON_MAIN" and out["reason_code"] == "UNSET"


def test_the_runner_exits_nonzero_unless_the_freeze_is_ok():
    from scripts.run_strategy_forward import exit_code
    assert exit_code({"status": "OK", "freeze": {"status": "FROZEN"}}) == 0
    assert exit_code({"status": "OK", "freeze": {"status": "FREEZE_PENDING_DATA"}}) == 1
    assert exit_code({"status": "ATTENTION", "freeze": {"status": "FROZEN"}}) == 1


def test_every_tracked_source_exists_and_the_fingerprint_is_the_plain_concatenation():
    import hashlib
    from pathlib import Path
    root = Path(f.__file__).resolve().parents[1]
    assert f.missing_sources() == []
    assert {"agent_intelligence/fair_value_forward.py", "agent_intelligence/inference.py"} <= set(f.DECISION_SOURCES)
    plain = hashlib.sha256(b"".join((root / p).read_bytes() for p in f.DECISION_SOURCES)).hexdigest()
    assert f.source_fingerprint() == plain
    assert not any(path in str(f.PROTOCOL) for path in f.DECISION_SOURCES)
