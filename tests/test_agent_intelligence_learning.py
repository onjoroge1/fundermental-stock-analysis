import pytest

from stock_machine.agent_intelligence import bandit, reward, tool_lab


def state():
    return {"bias_score":.5,"signal_components":{"fundamental":.8,"technical":.4,"news":.1,"regime":.2},
            "technical":{"features":{"realized_vol_20":.3}}}


def test_contextual_bandit_explores_uncertain_arms_in_paper_only():
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},mode="PAPER")
    assert result["exploration_enabled"] is True
    assert result["choice_driver"] == "EXPLORE_UNTRIED"
    assert result["selected"]["ucb"] == pytest.approx(
        result["selected"]["mean"] + result["selected"]["exploration_bonus"]
    )
    assert result["broker_submission"] is False
    assert result["selected"]["action"] in {"NO_TRADE","LONG_STOCK"}


def test_bandit_update_changes_arm_state():
    x=bandit.context_vector(state())
    arm=bandit.empty_arm(len(x))
    updated=bandit.update(arm,x,1.5)
    assert updated["observations"]==1
    assert updated["reward_sum"]==1.5


def test_bandit_forbids_live_mode():
    try:
        bandit.select(state(),["NO_TRADE"],mode="LIVE")
        assert False
    except ValueError as e:
        assert str(e)=="BANDIT_LIVE_MODE_FORBIDDEN"


def test_reward_is_net_return_in_ex_ante_risk_units():
    args=dict(gross_return_pct=5,max_drawdown_pct=-2,capital_used_pct=1,turnover_pct=2,costs_pct=.2,risk_scale_pct=8)
    r=reward.compute(**args)
    assert r["schema_version"]==reward.VERSION
    assert r["reward"]==pytest.approx((5-.2)/8)
    # Costs lower the reward; path drawdown and sizing are diagnostics only.
    assert reward.compute(**{**args,"costs_pct":.5})["reward"]<r["reward"]
    assert reward.compute(**{**args,"max_drawdown_pct":-10,"capital_used_pct":20,"turnover_pct":40})["reward"]==r["reward"]


def test_abstention_scores_zero_and_positive_edge_beats_it_on_average():
    import random
    from statistics import mean
    zero=reward.compute(gross_return_pct=0,max_drawdown_pct=0,capital_used_pct=0,turnover_pct=0,costs_pct=0,risk_scale_pct=9.86)
    assert zero["reward"]==0
    # v3 scored a +2%/20-session edge at about -3.5 (below abstention) via the drawdown penalty.
    rng=random.Random(7)
    rewards=[reward.compute(gross_return_pct=rng.gauss(2.0,9.86),max_drawdown_pct=-rng.uniform(0,15),
                            capital_used_pct=.93,turnover_pct=1.85,costs_pct=.2,risk_scale_pct=9.86)["reward"]
             for _ in range(4000)]
    assert mean(rewards)>0.1


def test_risk_scale_uses_decision_time_volatility_with_documented_fallbacks():
    scale=reward.risk_scale({"realized_vol_20":.35},20)
    assert scale["basis"]=="realized_vol_20"
    assert scale["risk_scale_pct"]==pytest.approx(.35*(20/252)**.5*100,rel=1e-6)
    assert reward.risk_scale({"realized_vol_20":None,"realized_vol_60":.3},20)["basis"]=="realized_vol_60"
    assert reward.risk_scale({},20)["basis"]=="DEFAULT_ANNUAL_VOL"
    assert reward.risk_scale({"realized_vol_20":.001},20)["annual_vol"]==reward.MIN_ANNUAL_VOL
    with pytest.raises(ValueError,match="REWARD_INPUT_INVALID"):
        reward.risk_scale({},0)


def test_bandit_ignores_arms_trained_under_an_earlier_reward_contract():
    from stock_machine.agent_intelligence.learning import current_arms
    arm={"a_diag":[2.0]*6,"b":[-3.0]*6,"observations":1,"reward_sum":-3.0}
    assert current_arms({"payload":{"arms":{"LONG_STOCK":arm}}})=={}
    assert current_arms({"payload":{"reward_version":reward.VERSION,"arms":{"LONG_STOCK":arm}}})=={"LONG_STOCK":arm}
    assert current_arms(None)=={}


def test_tool_lab_accepts_declarative_tool_and_rejects_code():
    spec={"name":"trend_quality","operation":"weighted_sum",
          "inputs":["signal_components.fundamental","signal_components.technical"],
          "weights":[.6,.4],"hypothesis":"combined fundamental and technical state may improve paper selection"}
    result=tool_lab.evaluate(spec,state())
    assert result["execution"]=="DECLARATIVE_SANDBOX"
    assert result["promotion"]=="NOT_AUTHORIZED"
    try:
        tool_lab.validate({"name":"bad","operation":"python","inputs":["x"],"hypothesis":"x"})
        assert False
    except ValueError as e:
        assert str(e)=="TOOL_OPERATION_NOT_ALLOWED"
