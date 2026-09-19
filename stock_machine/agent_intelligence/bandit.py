"""Contextual explore/exploit for PAPER/SHADOW only.

Disjoint diagonal LinUCB keeps the implementation transparent and auditable.
No arm selection produced here can authorize broker execution.
"""
from __future__ import annotations

from math import sqrt

FEATURE_NAMES=("fundamental","technical","news","regime","bias","volatility")


def context_vector(state: dict) -> list[float]:
    parts=state.get("signal_components") or {}
    tech=(state.get("technical") or {}).get("features") or {}
    vol=tech.get("realized_vol_20")
    return [
        float(parts.get("fundamental") or 0.0),
        float(parts.get("technical") or 0.0),
        float(parts.get("news") or 0.0),
        float(parts.get("regime") or 0.0),
        float(state.get("bias_score") or 0.0),
        min(1.0,max(0.0,float(vol or 0.0))),
    ]


def empty_arm(width: int | None=None) -> dict:
    width=width or len(FEATURE_NAMES)
    return {"a_diag":[1.0]*width,"b":[0.0]*width,"observations":0,
            "reward_sum":0.0}


def estimate(arm: dict, x: list[float], alpha: float=.35) -> dict:
    a=list(arm.get("a_diag") or [])
    b=list(arm.get("b") or [])
    if len(a)!=len(x) or len(b)!=len(x):
        raise ValueError("BANDIT_STATE_WIDTH_MISMATCH")
    theta=[b_i/a_i for a_i,b_i in zip(a,b)]
    mean=sum(t*v for t,v in zip(theta,x))
    uncertainty=sqrt(sum((v*v)/a_i for a_i,v in zip(a,x)))
    return {"mean":mean,"uncertainty":uncertainty,
            "ucb":mean+alpha*uncertainty}


def select(state: dict, actions: list[str], arm_states: dict[str,dict] | None=None,
           *, alpha: float=.35, mode: str="PAPER") -> dict:
    if mode not in {"PAPER","SHADOW"}:
        raise ValueError("BANDIT_LIVE_MODE_FORBIDDEN")
    x=context_vector(state)
    arm_states=arm_states or {}
    scored=[]
    for action in actions:
        arm=arm_states.get(action) or empty_arm(len(x))
        est=estimate(arm,x,alpha)
        scored.append({"action":action,**est,
                       "observations":int(arm.get("observations") or 0)})
    selected=max(scored,key=lambda r:(r["ucb"],-r["observations"],r["action"]))
    return {"schema_version":"contextual-bandit.v1","mode":mode,
            "alpha":alpha,"features":dict(zip(FEATURE_NAMES,x)),
            "selected":selected,"arms":scored,
            "exploration_enabled":True,"broker_submission":False}


def update(arm: dict, x: list[float], reward: float) -> dict:
    out={"a_diag":list(arm.get("a_diag") or [1.0]*len(x)),
         "b":list(arm.get("b") or [0.0]*len(x)),
         "observations":int(arm.get("observations") or 0),
         "reward_sum":float(arm.get("reward_sum") or 0.0)}
    if len(out["a_diag"])!=len(x) or len(out["b"])!=len(x):
        raise ValueError("BANDIT_STATE_WIDTH_MISMATCH")
    for i,value in enumerate(x):
        out["a_diag"][i]+=value*value
        out["b"][i]+=reward*value
    out["observations"]+=1
    out["reward_sum"]+=float(reward)
    return out
