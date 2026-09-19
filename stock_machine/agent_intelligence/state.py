"""Unified, inspectable context state for Agent Intelligence v2."""
from __future__ import annotations


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def assemble(ticker: str, bundle: dict, technical: dict, news: dict,
             *, option_surface: dict | None = None,
             regime: dict | None = None) -> dict:
    fundamental = bundle.get("fundamental_scores") or {}
    score = fundamental.get("composite_score")
    f_signal = None if score is None else _clip((float(score)-60.0)/20.0)

    tf = technical.get("features") or {}
    tc = technical.get("classification") or {}
    t_parts=[]
    if tf.get("momentum_63") is not None:
        t_parts.append(_clip(float(tf["momentum_63"])/0.20))
    if tc.get("trend_positive_vote_share") is not None:
        t_parts.append(_clip((float(tc["trend_positive_vote_share"])-.5)*2))
    if tf.get("relative_momentum_63_vs_spy") is not None:
        t_parts.append(_clip(float(tf["relative_momentum_63_vs_spy"])/0.15))
    t_signal=sum(t_parts)/len(t_parts) if t_parts else None

    nf=news.get("features") or {}
    n_signal=(None if news.get("status")!="OK"
              else _clip(nf.get("signed_event_pressure") or 0.0))

    r_signal=None
    if regime and regime.get("status")=="OK":
        share=regime.get("risk_on_vote_share")
        if share is not None:
            r_signal=_clip((float(share)-.5)*2)

    pieces=[(.50,f_signal,"fundamental"),(.25,t_signal,"technical"),
            (.15,n_signal,"news"),(.10,r_signal,"regime")]
    present=[(w,v,n) for w,v,n in pieces if v is not None]
    total=sum(w for w,_,_ in present)
    bias=sum(w*v for w,v,_ in present)/total if total else 0.0
    if bias>=.25:
        direction="BULLISH"
    elif bias<=-.25:
        direction="BEARISH"
    else:
        direction="NEUTRAL"

    blockers=[]
    quality=bundle.get("data_quality") or {}
    if quality.get("status")!="PASS":
        blockers.append("DATA_QUALITY_NOT_PASS")
    if (quality.get("financial_integrity") or {}).get("status")!="VERIFIED":
        blockers.append("FINANCIAL_INTEGRITY_NOT_VERIFIED")
    if nf.get("high_materiality_negative"):
        blockers.append("HIGH_MATERIALITY_NEGATIVE_HEADLINE_CONTEXT")

    return {
        "schema_version":"agent-state.v2",
        "ticker":ticker.upper(),
        "as_of":(bundle.get("market_snapshot") or {}).get("price_date"),
        "direction":direction,
        "bias_score":round(bias,4),
        "signal_components":{n:(None if v is None else round(v,4)) for _,v,n in pieces},
        "component_weights_used":{n:round(w/total,4) for w,v,n in present} if total else {},
        "fundamental_score":score,
        "technical":technical,
        "news":news,
        "regime":regime,
        "option_surface":option_surface,
        "blockers":blockers,
        "paper_eligible":not blockers,
        "limitations":[
            "bias is a transparent heuristic state summary, not a calibrated return forecast",
            "headline context is unverified metadata",
            "option-surface features are descriptive unless backed by a current observed snapshot",
        ],
    }
