import pytest
from stock_machine.agent_intelligence.strategy_router import route, eligible_actions
from stock_machine.agent_intelligence.instructions import paper_instruction


def state(**overrides):
    return {"direction": "BULLISH", "bias_score": .8, "paper_eligible": True, "blockers": [], **overrides}


def option(key="one", rank=90, **payoff):
    return {"candidate_id": key, "strategy_type": "bull_call_debit_spread",
            "payoff": {"defined_risk": True, "max_loss": 400., **payoff},
            "liquidity": {"passed": True, "score": .9}, "ranking": {"total": rank}}


def test_explanations_are_not_blockers():
    actions = eligible_actions(route(state()))
    assert set(actions) == {"LONG_STOCK", "NO_TRADE"}
    assert actions["LONG_STOCK"]["reasons"] and not actions["LONG_STOCK"]["blockers"]
    assert "SHORT_STOCK" in eligible_actions(route(state(direction="BEARISH", bias_score=-.8)))


def test_state_veto_masks_options_and_stocks_before_bandit():
    result = route(state(blockers=["EVENT_RISK"]), [option()])
    assert set(eligible_actions(result)) == {"NO_TRADE"}
    assert result["selected"]["action"] == "NO_TRADE"


@pytest.mark.parametrize("loss", [float("nan"), float("inf"), -1, 0, True, None, 1001])
def test_invalid_option_risk_never_reaches_selection(loss):
    assert not any(k.startswith("OPTION:") for k in eligible_actions(route(state(), [option(max_loss=loss)])))


def test_best_candidate_not_last_candidate_wins_per_arm():
    actions = eligible_actions(route(state(), [option("best", 95), option("worse", 50)]))
    assert actions["OPTION:bull_call_debit_spread"]["candidate_id"] == "best"


@pytest.mark.parametrize("result", [None, {}, {"status": "UNAVAILABLE"}, {"paper_instruction": {"desired_side": "LONG"}}])
def test_missing_intelligence_blocks_old_selector_fallback(result):
    instruction = paper_instruction(result, "PAPER")
    assert instruction["blocker"] == "INTELLIGENCE_V2_UNAVAILABLE"
    assert instruction["desired_side"] == "FLAT"
    assert paper_instruction(result, "RESEARCH") is None


def test_nan_state_never_becomes_a_directional_action():
    assert set(eligible_actions(route(state(bias_score=float("nan"))))) == {"NO_TRADE"}


def test_valid_instruction_preserves_explicit_exit_and_blocks():
    instruction = {"source": "agent-intelligence.v2", "desired_side": "FLAT", "selected_action": "NO_TRADE"}
    assert paper_instruction({"paper_instruction": instruction}, "PAPER") == instruction


def test_orchestrator_freezes_clock_and_replays_before_rebuilding(monkeypatch):
    from stock_machine.agent_intelligence import orchestrator as o
    from stock_machine import db, research_store
    from stock_machine.options import surface_store
    from datetime import datetime, timezone
    records, calls = {}, []
    decision = {"ticker": "AAPL", "decision_id": "decision", "input_sha256": "hash", "status": "RECORDED",
                "price_date": "2020-01-02", "observed_at": "2020-01-02T22:00:00Z"}
    packet = {"ticker": "AAPL", "market_snapshot": {"price_date": "2020-01-02"}}
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args): return self
        def fetchone(self): return (packet,)
    monkeypatch.setattr(db, "connect", Conn)
    monkeypatch.setattr(research_store, "get", lambda conn, kind, key: records.get(key))
    monkeypatch.setattr(research_store, "latest", lambda *a: None)
    monkeypatch.setattr(research_store, "save", lambda conn, kind, key, value, ticker: records.update({key: {"payload": value}}))
    monkeypatch.setattr(surface_store, "latest_as_of", lambda *a, **k: None)
    monkeypatch.setattr(o, "build_for_ticker", lambda *a, **k: calls.append("features") or {})
    monkeypatch.setattr(o, "_regime", lambda *a: {})
    def news(value, *, now):
        assert now == datetime(2020, 1, 2, 22, tzinfo=timezone.utc)
        return {}
    monkeypatch.setattr(o, "build_news", news)
    monkeypatch.setattr(o, "assemble", lambda *a, **k: state())
    def choose(context, actions, arms, mode):
        assert "LONG_STOCK" in actions
        return {"selected": {"action": "LONG_STOCK"}}
    monkeypatch.setattr(o.bandit, "select", choose)
    first = o.evaluate_decision(decision, mode="PAPER")
    second = o.evaluate_decision(decision, mode="PAPER")
    assert second == {**first, "replayed": True}
    assert calls == ["features"]
    with pytest.raises(ValueError, match="MODE_CONFLICT"):
        o.evaluate_decision(decision, mode="SHADOW")
