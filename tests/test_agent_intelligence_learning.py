import pytest

from stock_machine.agent_intelligence import bandit, reward, tool_lab


def state():
    return {"bias_score":.5,"signal_components":{"fundamental":.8,"technical":.4,"news":.1,"regime":.2},
            "technical":{"features":{"realized_vol_20":.3}}}


def test_cold_start_abstains_and_reports_the_confidence_gate():
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{},mode="PAPER",decision_key="d1")
    assert result["selection"]=="posterior-confidence" and result["exploration_enabled"] is False
    assert result["selected"]["action"]=="NO_TRADE" and result["choice_driver"]=="ABSTAIN_NO_EDGE"
    for arm in result["arms"]:
        assert arm["ucb"]==pytest.approx(arm["mean"]-bandit.CONFIDENCE_Z*arm["uncertainty"])
        assert arm["exploration_bonus"]==pytest.approx(arm["score"]-arm["mean"])
    assert result["broker_submission"] is False

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
    assert (baseline["mean"],baseline["uncertainty"],baseline["score"])==(0.0,0.0,0.0)
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


def test_uncertain_edge_waits_and_counterfactual_labels_recover_from_a_bad_start():
    # Labels arrive for every decision whatever is traded, so an early bad
    # draw cannot lock an arm out: later evidence still updates it.
    x=bandit.context_vector(state())
    arm=bandit.update(bandit.empty_arm(len(x)),x,-2.0)
    for _ in range(3):
        arm=bandit.update(arm,x,0.5)
    unsure=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{"LONG_STOCK":arm})
    assert unsure["selected"]["action"]=="NO_TRADE"
    for _ in range(200):
        arm=bandit.update(arm,x,0.5)
    sure=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{"LONG_STOCK":arm})
    assert sure["selected"]["action"]=="LONG_STOCK" and sure["choice_driver"]=="EXPLOIT_CONFIDENT"


def test_positive_but_unproven_edge_is_reported_as_uncertain():
    x=bandit.context_vector(state())
    arm=bandit.empty_arm(len(x))
    for _ in range(2):
        arm=bandit.update(arm,x,0.4)
    result=bandit.select(state(),["NO_TRADE","LONG_STOCK"],{"LONG_STOCK":arm})
    assert result["selected"]["action"]=="NO_TRADE" and result["choice_driver"]=="ABSTAIN_UNCERTAIN"


def test_sector_offset_is_shrunk_and_adds_its_own_uncertainty():
    x=bandit.context_vector(state())
    arm=bandit.empty_arm(len(x))
    assert bandit.sector_offset({},"tech")==(0.0,pytest.approx(1/bandit.SECTOR_PRIOR_PRECISION))
    offsets=bandit.update_sector_offset(None,"tech",0.3,1.0)
    for _ in range(99):
        offsets=bandit.update_sector_offset(offsets,"tech",0.3,1.0)
    mean,var=bandit.sector_offset(offsets,"tech")
    assert mean==pytest.approx(0.3*100/(100+bandit.SECTOR_PRIOR_PRECISION))   # half-shrunk at 100 obs
    assert var==pytest.approx(1/200)
    with_sector=bandit.predict(arm,x,offsets,"tech"); without=bandit.predict(arm,x,offsets,None)
    assert with_sector["mean"]==pytest.approx(without["mean"]+mean)
    assert with_sector["uncertainty"]>without["uncertainty"]
    assert bandit.update_sector_offset(offsets,None,1.0,1.0)==offsets   # unknown sector: unchanged

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
    from stock_machine.agent_intelligence.learning import LEARNING_BASIS, POOLED_SCOPE, current_arms
    arm=bandit.empty_arm()
    current={"scope":POOLED_SCOPE,"reward_version":reward.VERSION,"bandit_version":bandit.VERSION,
             "learning_basis":LEARNING_BASIS,"arms":{"LONG_STOCK":arm}}
    assert current_arms(current)=={"LONG_STOCK":arm}
    assert current_arms({**current,"reward_version":"risk-adjusted-paper-reward.v3"})=={}
    assert current_arms({**current,"bandit_version":"contextual-bandit.v1"})=={}
    assert current_arms({**current,"learning_basis":"REALIZED_PAPER_FILL_V1"})=={}
    assert current_arms({"reward_version":reward.VERSION,"bandit_version":bandit.VERSION,"arms":{"LONG_STOCK":arm}})=={}
    assert current_arms(None)=={}


def test_overlap_weight_scales_information_not_estimate():
    x=bandit.context_vector(state())
    full=bandit.update(bandit.empty_arm(len(x)),x,1.0)
    tiny=bandit.update(bandit.empty_arm(len(x)),x,1.0,weight=1/20)
    assert tiny["effective_observations"]==pytest.approx(1/20)
    assert bandit.estimate(tiny,x)["uncertainty"]>bandit.estimate(full,x)["uncertainty"]
    twenty=bandit.empty_arm(len(x))
    for _ in range(20):
        twenty=bandit.update(twenty,x,1.0,weight=1/20)
    assert bandit.estimate(twenty,x)["mean"]==pytest.approx(bandit.estimate(full,x)["mean"])
    with pytest.raises(ValueError,match="BANDIT_WEIGHT_INVALID"):
        bandit.update(full,x,1.0,weight=0)


def test_missing_signals_are_flagged_not_treated_as_neutral():
    neutral={"signal_components":{"fundamental":0.0,"technical":0.0,"news":0.0,"regime":0.0},
             "technical":{"features":{"realized_vol_20":.35}}}
    missing={"signal_components":{"fundamental":None,"technical":0.0,"news":None,"regime":0.0},
             "technical":{"features":{}}}
    names=bandit.FEATURE_NAMES
    a,b=dict(zip(names,bandit.context_vector(neutral))),dict(zip(names,bandit.context_vector(missing)))
    assert a!=b
    assert (b["fundamental_missing"],b["news_missing"],b["volatility_missing"])==(1.0,1.0,1.0)
    assert (b["technical_missing"],b["regime_missing"])==(0.0,0.0)
    assert not any(v for k,v in a.items() if k.endswith("_missing"))
    for bad in (float("nan"),True,"0.4"):
        x=dict(zip(names,bandit.context_vector({"signal_components":{"fundamental":bad}})))
        assert x["fundamental"]==0.0 and x["fundamental_missing"]==1.0


def test_volatility_is_standardized_and_clipped_not_a_second_intercept():
    def vol(v):
        return dict(zip(bandit.FEATURE_NAMES,bandit.context_vector({"technical":{"features":{"realized_vol_20":v}}})))
    assert vol(.35)["volatility_z"]==0.0
    assert vol(.60)["volatility_z"]==pytest.approx(1.0)
    assert vol(5.0)["volatility_z"]==3.0 and vol(.01)["volatility_z"]==pytest.approx(-1.36)
    assert vol(0)["volatility_missing"]==1.0 and vol(0)["volatility_z"]==0.0


def test_contract_changes_cold_start_the_pooled_model():
    from stock_machine.agent_intelligence.learning import LEARNING_BASIS, POOLED_SCOPE, current_arms, current_offsets
    assert bandit.VERSION=="pooled-linear-confident-sector.v3"
    assert reward.VERSION=="risk-scaled-residual-paper-reward.v5"
    old={"scope":POOLED_SCOPE,"reward_version":reward.VERSION,"bandit_version":"pooled-linear-thompson.v2",
         "learning_basis":LEARNING_BASIS,"arms":{"LONG_STOCK":{"precision":[[1.0]*6]*6}},"offsets":{"LONG_STOCK":{}}}
    assert current_arms(old)=={} and current_offsets(old)=={}
