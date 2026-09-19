"""Deterministic headline-event features for Agent Intelligence v2.

Massive news is metadata only. Titles are untrusted context, never verified
facts. This module extracts bounded event features for paper/shadow research.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

EVENT_RULES = (
    ("GUIDANCE_RAISE", ("raises guidance","raises outlook","boosts outlook","raises forecast"), 0.85, 1),
    ("GUIDANCE_CUT", ("cuts guidance","lowers guidance","cuts outlook","lowers outlook","warning"), 0.90, -1),
    ("EARNINGS_BEAT", ("beats estimates","beats expectations","earnings beat","revenue beat"), 0.75, 1),
    ("EARNINGS_MISS", ("misses estimates","misses expectations","earnings miss","revenue miss"), 0.80, -1),
    ("REGULATORY_ACTION", ("investigation","subpoena","antitrust","regulator","regulatory","ftc","doj","sec probe"), 0.85, -1),
    ("LITIGATION", ("lawsuit","sued","litigation","settlement"), 0.60, -1),
    ("M_AND_A", ("acquire","acquisition","merger","to buy","takeover"), 0.65, 0),
    ("CAPITAL_RAISE", ("stock offering","share offering","convertible notes","debt offering","capital raise"), 0.65, -1),
    ("BUYBACK", ("buyback","share repurchase","repurchase authorization"), 0.60, 1),
    ("MANAGEMENT_CHANGE", ("ceo resigns","ceo departs","cfo resigns","appoints ceo","new ceo"), 0.60, 0),
    ("PRODUCT_EVENT", ("launches","launch","approval","approved","clearance","product recall","recall"), 0.55, 0),
    ("CUSTOMER_CONTRACT", ("contract win","wins contract","partnership","customer deal","agreement with"), 0.55, 1),
    ("LAYOFF", ("layoffs","job cuts","cuts jobs","workforce reduction"), 0.50, 0),
)

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(title: str) -> set[str]:
    return set(TOKEN_RE.findall(title.lower()))


def _similarity(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def classify_title(title: str) -> dict:
    lowered = str(title or "").lower()
    hits = []
    for event, phrases, materiality, direction in EVENT_RULES:
        matched = [p for p in phrases if p in lowered]
        if matched:
            hits.append({"event_type": event, "materiality": materiality,
                         "direction": direction, "matched_phrases": matched})
    if not hits:
        return {"event_type": "OTHER", "materiality": 0.20,
                "direction": 0, "matched_phrases": []}
    return max(hits, key=lambda row: (row["materiality"], abs(row["direction"]), row["event_type"]))


def build(news: dict | None, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    payload = news or {}
    articles = payload.get("articles") or []
    if payload.get("status") not in {"OBSERVED","NO_ARTICLES_RETURNED"}:
        return {"schema_version":"news-events.v1","status":"UNAVAILABLE",
                "events":[],"features":{},"limitations":["news metadata unavailable"]}
    events=[]
    seen_titles=[]
    counts={}
    signed=0.0
    max_materiality=0.0
    for article in articles[:5]:
        title=str(article.get("title") or "")[:500]
        if not title:
            continue
        duplicate=max((_similarity(title,x) for x in seen_titles), default=0.0) >= .75
        seen_titles.append(title)
        try:
            published=datetime.fromisoformat(str(article.get("published_utc")).replace("Z","+00:00"))
            if published.tzinfo is None:
                raise ValueError
            age_hours=max(0.0,(now-published.astimezone(timezone.utc)).total_seconds()/3600)
        except (ValueError,TypeError):
            age_hours=None
        cls=classify_title(title)
        decay=(math.exp(-age_hours/(24*3)) if age_hours is not None else 0.0)
        novelty=0.25 if duplicate else 1.0
        weighted=cls["materiality"]*decay*novelty
        signed += weighted*cls["direction"]
        max_materiality=max(max_materiality,weighted)
        counts[cls["event_type"]]=counts.get(cls["event_type"],0)+1
        events.append({
            "source_id":article.get("source_id"),
            "published_utc":article.get("published_utc"),
            "event_type":cls["event_type"],
            "headline_materiality":round(cls["materiality"],3),
            "headline_direction":cls["direction"],
            "time_decay":round(decay,3),
            "novelty_weight":novelty,
            "weighted_materiality":round(weighted,3),
            "duplicate_like":duplicate,
            "title":title,
        })
    return {
        "schema_version":"news-events.v1",
        "status":"OK",
        "events":events,
        "features":{
            "article_count":len(events),
            "event_counts":counts,
            "max_weighted_materiality":round(max_materiality,3),
            "signed_event_pressure":round(signed,3),
            "high_materiality_negative":any(e["weighted_materiality"]>=.5 and e["headline_direction"]<0 for e in events),
            "high_materiality_positive":any(e["weighted_materiality"]>=.5 and e["headline_direction"]>0 for e in events),
        },
        "limitations":[
            "headline metadata only; article contents are not reviewed",
            "event direction is a deterministic title heuristic, not verified sentiment or causal impact",
            "features may gate or contextualize PAPER research but cannot establish a financial fact",
        ],
    }
