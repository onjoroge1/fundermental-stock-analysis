"""Shared automated/manual agent cycle contracts."""
from __future__ import annotations

import pytest

import stock_machine.agent_cycle as cycle


def _decision(**overrides):
    value = {
        "decision_id": "11111111-1111-4111-8111-111111111111",
        "ticker": "AAPL",
        "mode": "RESEARCH",
        "execution_status": "NOT_ENABLED",
        "status": "RECORDED",
        "action": "WATCH",
        "price_date": "2026-09-18",
        "blockers": [],
    }
    value.update(overrides)
    return value


def test_agent_cycle_chains_research_journal_and_paper(monkeypatch):
    import stock_machine.research_cycle as research
    from stock_machine.agents import journal
    from stock_machine import agent_trading

    calls = []
    monkeypatch.setattr(research, "run", lambda ticker, key: {
        "status": "COMPLETED", "provider_status": "OBSERVED",
        "news_status": "OBSERVED", "claims_verified": 7, "report_id": "brief-1",
    })
    monkeypatch.setattr(journal, "capture", lambda ticker, request: (
        calls.append(("journal", ticker, request.idempotency_key))
        or {"replayed": False, "decision": _decision(ticker=ticker)}
    ))
    monkeypatch.setattr(agent_trading, "process_decision", lambda decision: (
        calls.append(("paper", decision["decision_id"]))
        or {"status": "SIMULATED", "execution_mode": "PAPER",
            "broker_submission": False, "action": "OPEN_LONG"}
    ))
    monkeypatch.setattr(agent_trading, "mark_open_positions", lambda: (
        calls.append(("mark", "portfolio"))
        or {"status": "OK", "open_count": 1, "broker_submission": False}
    ))
    monkeypatch.setattr(agent_trading, "mark_open_positions", lambda: (
        calls.append(("mark", "portfolio"))
        or {"status": "OK", "open_count": 1, "broker_submission": False}
    ))
    guards = []
    result = cycle.run("AAPL", "auto:research_cycle:AAPL:2026-09-18",
                       capture_guard=lambda: guards.append("checked"))

    assert guards == ["checked"]
    assert calls[0][:2] == ("journal", "AAPL")
    assert calls[1][0] == "paper"
    assert calls[2][0] == "mark"
    assert result["paper_mark"]["status"] == "OK"
    assert result["decision_status"] == "RECORDED"
    assert result["trade_execution"] is True
    assert result["broker_submission"] is False


def test_busy_cycle_never_journals_or_trades(monkeypatch):
    import stock_machine.research_cycle as research
    from stock_machine.agents import journal
    from stock_machine import agent_trading

    monkeypatch.setattr(research, "run", lambda *a, **k: {"status": "BUSY"})
    monkeypatch.setattr(journal, "capture", lambda *a, **k: pytest.fail("journal should not run"))
    monkeypatch.setattr(agent_trading, "process_decision", lambda *a, **k: pytest.fail("paper should not run"))
    result = cycle.run("AAPL", "auto:test")
    assert result["status"] == "BUSY"
    assert result["trade_execution"] is False


def test_agent_cycle_rejects_any_research_execution_boundary_change(monkeypatch):
    import stock_machine.research_cycle as research
    from stock_machine.agents import journal

    monkeypatch.setattr(research, "run", lambda *a, **k: {"status": "COMPLETED"})
    monkeypatch.setattr(journal, "capture", lambda *a, **k: {
        "replayed": False,
        "decision": _decision(execution_status="ENABLED"),
    })
    with pytest.raises(RuntimeError, match="RESEARCH_BOUNDARY_VIOLATION"):
        cycle.run("AAPL", "auto:test")


def test_automated_control_plane_dispatches_shared_agent_cycle(monkeypatch):
    import stock_machine.control_plane as cp

    calls = []
    monkeypatch.setattr(cycle, "run", lambda ticker, key: (
        calls.append((ticker, key))
        or {"status": "COMPLETED", "broker_submission": False}
    ))
    result = cp.execute({
        "job_type": "research_cycle",
        "ticker": "AAPL",
        "payload": {},
        "idempotency_key": "auto:research_cycle:AAPL:2026-09-18",
    })
    assert calls == [("AAPL", "auto:research_cycle:AAPL:2026-09-18")]
    assert result["broker_submission"] is False


def test_paper_mark_failure_does_not_rewrite_completed_decision(monkeypatch):
    import stock_machine.research_cycle as research
    from stock_machine.agents import journal
    from stock_machine import agent_trading

    monkeypatch.setattr(research, "run", lambda *a, **k: {"status": "COMPLETED"})
    monkeypatch.setattr(journal, "capture", lambda *a, **k: {
        "replayed": False, "decision": _decision()
    })
    monkeypatch.setattr(agent_trading, "process_decision", lambda decision: {
        "status": "NO_ACTION", "execution_mode": "PAPER",
        "broker_submission": False, "action": "HOLD"
    })
    monkeypatch.setattr(agent_trading, "mark_open_positions",
                        lambda: (_ for _ in ()).throw(ValueError("PAPER_MARK_PRICE_MISSING")))

    result = cycle.run("AAPL", "auto:test-mark")
    assert result["decision_status"] == "RECORDED"
    assert result["paper_mark"] == {
        "status": "BLOCKED", "reason": "PAPER_MARK_PRICE_MISSING",
        "broker_submission": False,
    }
    assert result["broker_submission"] is False


def test_paper_mark_failure_does_not_rewrite_completed_decision(monkeypatch):
    import stock_machine.research_cycle as research
    from stock_machine.agents import journal
    from stock_machine import agent_trading

    monkeypatch.setattr(research, "run", lambda *a, **k: {"status": "COMPLETED"})
    monkeypatch.setattr(journal, "capture", lambda *a, **k: {
        "replayed": False, "decision": _decision()
    })
    monkeypatch.setattr(agent_trading, "process_decision", lambda decision: {
        "status": "NO_ACTION", "execution_mode": "PAPER",
        "broker_submission": False, "action": "HOLD"
    })
    def fail_mark():
        raise ValueError("PAPER_MARK_PRICE_MISSING")
    monkeypatch.setattr(agent_trading, "mark_open_positions", fail_mark)

    result = cycle.run("AAPL", "auto:test-mark")
    assert result["decision_status"] == "RECORDED"
    assert result["paper_mark"]["status"] == "BLOCKED"
    assert result["paper_mark"]["reason"] == "PAPER_MARK_PRICE_MISSING"
    assert result["broker_submission"] is False
