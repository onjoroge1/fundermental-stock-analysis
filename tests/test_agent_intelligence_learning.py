from stock_machine.agent_intelligence import bandit, reward, tool_lab


def state():
    return {"bias_score":.5,"signal_components":{"fundamental":.8,"technical":.4,"news":.1,"regime":.2},
            "technical":{"features":{"realized_vol_20":.3}}}


def test_contextual_bandit_explores_uncertain_arms_in_paper_only():
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},mode="PAPER")
    assert result["exploration_enabled"] is True
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


def test_reward_penalizes_drawdown_cost_and_capital():
    good=reward.compute(net_return_pct=5,max_drawdown_pct=-2,capital_used_pct=10,turnover_pct=5,costs_pct=.2)
    bad=reward.compute(net_return_pct=5,max_drawdown_pct=-10,capital_used_pct=20,turnover_pct=20,costs_pct=.5)
    assert good["reward"]>bad["reward"]


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
