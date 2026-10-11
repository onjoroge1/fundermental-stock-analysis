"""The learned direction acts only through the owner gate."""

from stock_machine.agent_intelligence import direction_policy


def state(**overrides):
    return {"direction": "BULLISH", "paper_eligible": True, **overrides}


def test_learned_policy_routes_on_the_frozen_challenger():
    frozen = {"incumbent": "LONG", "challenger": "SHORT"}
    routed = direction_policy.apply(state(), frozen, {"policy": "LEARNED"})
    assert routed["direction"] == "BEARISH" and routed["heuristic_direction"] == "BULLISH"
    assert routed["direction_source"] == "LEARNED"
    flat = direction_policy.apply(state(), {"challenger": "FLAT"}, {"policy": "LEARNED"})
    assert flat["direction"] == "NEUTRAL"


def test_heuristic_or_ineligible_state_is_untouched():
    frozen = {"challenger": "SHORT"}
    assert direction_policy.apply(state(), frozen, {"policy": "HEURISTIC"})["direction"] == "BULLISH"
    blocked = direction_policy.apply(state(paper_eligible=False), frozen, {"policy": "LEARNED"})
    assert blocked["direction"] == "BULLISH" and blocked["direction_source"] == "HEURISTIC"
