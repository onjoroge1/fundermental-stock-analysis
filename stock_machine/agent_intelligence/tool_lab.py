"""Constrained Agent Tool Lab v1.

Agents may propose declarative calculations over already-approved state fields.
Arbitrary Python, SQL, network, filesystem and subprocess execution are not
accepted by this layer.
"""
from __future__ import annotations

import re

NAME=re.compile(r"^[a-z][a-z0-9_]{2,63}$")
ALLOWED={"weighted_sum","difference","ratio","min","max"}


def validate(spec: dict) -> dict:
    name=str(spec.get("name") or "")
    operation=str(spec.get("operation") or "")
    inputs=list(spec.get("inputs") or [])
    if not NAME.fullmatch(name):
        raise ValueError("TOOL_NAME_INVALID")
    if operation not in ALLOWED:
        raise ValueError("TOOL_OPERATION_NOT_ALLOWED")
    if not 1<=len(inputs)<=12 or any(not isinstance(x,str) or len(x)>160 for x in inputs):
        raise ValueError("TOOL_INPUTS_INVALID")
    if not str(spec.get("hypothesis") or "").strip():
        raise ValueError("TOOL_HYPOTHESIS_REQUIRED")
    if operation=="weighted_sum":
        weights=list(spec.get("weights") or [])
        if len(weights)!=len(inputs) or any(not isinstance(w,(int,float)) for w in weights):
            raise ValueError("TOOL_WEIGHTS_INVALID")
    return {"name":name,"version":int(spec.get("version") or 1),
            "operation":operation,"inputs":inputs,
            "weights":list(spec.get("weights") or []),
            "hypothesis":str(spec["hypothesis"])[:1000],
            "status":"PROPOSED_SHADOW_ONLY"}


def _get(document: dict, path: str):
    value=document
    for part in path.split("."):
        if not isinstance(value,dict) or part not in value:
            raise ValueError("TOOL_INPUT_MISSING:"+path)
        value=value[part]
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError("TOOL_INPUT_NOT_NUMERIC:"+path)
    return float(value)


def evaluate(spec: dict, state: dict) -> dict:
    s=validate(spec)
    values=[_get(state,p) for p in s["inputs"]]
    op=s["operation"]
    if op=="weighted_sum":
        result=sum(v*float(w) for v,w in zip(values,s["weights"]))
    elif op=="difference":
        if len(values)!=2: raise ValueError("DIFFERENCE_REQUIRES_TWO_INPUTS")
        result=values[0]-values[1]
    elif op=="ratio":
        if len(values)!=2 or values[1]==0: raise ValueError("RATIO_INVALID")
        result=values[0]/values[1]
    elif op=="min":
        result=min(values)
    else:
        result=max(values)
    return {"tool":s,"result":result,
            "execution":"DECLARATIVE_SANDBOX",
            "promotion":"NOT_AUTHORIZED",
            "broker_submission":False}
