import pytest

from stock_machine.agent_intelligence import bandit, reward, tool_lab


def state():
    return {"bias_score":.5,"signal_components":{"fundamental":.8,"technical":.4,"news":.1,"regime":.2},
            "technical":{"features":{"realized_vol_20":.3}}}


def test_contextual_bandit_explores_uncertain_arms_in_paper_only():
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},mode="PAPER",decision_key="d1")
    assert result["exploration_enabled"] is True
    assert result["sampling"]=="thompson"
    for arm in result["arms"]:
        assert arm["ucb"]==pytest.approx(arm["mean"]+arm["exploration_bonus"])
    assert result["broker_submission"] is False
    assert result["selected"]["action"] in {"NO_TRADE","LONG_STOCK"}


def test_bandit_update_changes_arm_state():
    x=bandit.context_vector(state())
    arm=bandit.empty_arm(len(x))
    updated=bandit.update(arm,x,1.5)
    assert updated["observations"]==1
    assert updated["reward_sum"]==1.5
    assert bandit.estimate(updated,x)["mean"]>0
    assert bandit.estimate(updated,x)["uncertainty"]<bandit.estimate(arm,x)["uncertainty"]


def test_context_has_intercept_and_no_collinear_bias():
    x=bandit.context_vector(state())
    assert bandit.FEATURE_NAMES[0]=="intercept" and x[0]==1.0
    assert "bias" not in bandit.FEATURE_NAMES and len(x)==len(bandit.FEATURE_NAMES)


def test_no_trade_is_a_fixed_zero_baseline():
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},decision_key="d1")
    baseline=next(a for a in result["arms"] if a["action"]=="NO_TRADE")
    assert (baseline["mean"],baseline["uncertainty"],baseline["sample"])==(0.0,0.0,0.0)
    assert baseline["baseline"] is True


def test_selection_is_reproducible_from_the_decision_key():
    one=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},decision_key="same")
    two=bandit.select(state(),["LONG_STOCK","NO_TRADE"],{},decision_key="same")
    assert one["selected"]==two["selected"]


def test_learned_negative_edge_is_avoided_and_positive_edge_is_taken():
    x=bandit.context_vector(state())
    def trained(r):
        arm=bandit.empty_arm(len(x))
        for _ in range(200):
            arm=bandit.update(arm,x,r)
        return arm
    def long_share(arm):
        picks=[bandit.select(state(),["NO_TRADE","LONG_STOCK"],{"LONG_STOCK":arm},decision_key=str(i))["selected"]["action"]
               for i in range(200)]
        return picks.count("LONG_STOCK")/len(picks)
    assert long_share(trained(-0.5))<0.05
    assert long_share(trained(0.5))>0.95


def test_one_bad_draw_does_not_lock_out_an_arm():
    # The diagonal LinUCB never retried an arm after one bad reward at the
    # same context; the posterior keeps a meaningful chance of retrying.
    x=bandit.context_vector(state())
    def long_count(r):
        arm=bandit.update(bandit.empty_arm(len(x)),x,r)
        return [bandit.select(state(),["NO_TRADE","LONG_STOCK"],{"LONG_STOCK":arm},decision_key=str(i))["selected"]["action"]
                for i in range(400)].count("LONG_STOCK")
    assert long_count(-1.0)>60   # one-sigma loss: retried about a quarter of the time
    assert long_count(-2.0)>10   # two-sigma loss: still explored


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


def test_pooled_model_ignores_state_from_earlier_contracts():
    from stock_machine.agent_intelligence.learning import POOLED_SCOPE, current_arms
    arm=bandit.empty_arm()
    current={"scope":POOLED_SCOPE,"reward_version":reward.VERSION,"bandit_version":bandit.VERSION,"arms":{"LONG_STOCK":arm}}
    assert current_arms(current)=={"LONG_STOCK":arm}
    assert current_arms({**current,"reward_version":"risk-adjusted-paper-reward.v3"})=={}
    assert current_arms({**current,"bandit_version":"contextual-bandit.v1"})=={}
    assert current_arms({"reward_version":reward.VERSION,"bandit_version":bandit.VERSION,"arms":{"LONG_STOCK":arm}})=={}
    assert current_arms(None)=={}
