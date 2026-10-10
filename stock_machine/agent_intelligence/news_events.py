"""Deterministic headline-event features for Agent Intelligence v2.

Massive news is metadata only. Titles are untrusted context, never verified
facts. This module extracts bounded event features for paper/shadow research.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

# Patterns are regexes matched on word boundaries; plain substrings matched
# inside other words ("sued" in "issued") and are not used.
EVENT_RULES = (
    ("GUIDANCE_RAISE", (r"raises? (full[- ]year )?(guidance|outlook|forecast)", r"boosts? (guidance|outlook|forecast)"), 0.85, 1),
    ("GUIDANCE_CUT", (r"(cuts?|lowers?|slashes) (full[- ]year )?(guidance|outlook|forecast)", r"(profit|revenue|sales|earnings) warning"), 0.90, -1),
    ("EARNINGS_BEAT", (r"beats? (estimates|expectations|forecasts?)", r"(earnings|revenue|profit) beat"), 0.75, 1),
    ("EARNINGS_MISS", (r"miss(es)? (estimates|expectations|forecasts?)", r"(earnings|revenue|profit) miss"), 0.80, -1),
    ("REGULATORY_ACTION", (r"investigations?", r"subpoena(s|ed)?", r"antitrust", r"regulators?", r"regulatory", r"ftc", r"doj", r"sec probe"), 0.85, -1),
    ("LITIGATION", (r"lawsuits?", r"sued", r"sues", r"litigation", r"settlement"), 0.60, -1),
    ("M_AND_A", (r"acquires?", r"acquired", r"acquisitions?", r"mergers?", r"(agrees|deal|offer|bid) to buy", r"takeover"), 0.65, 0),
    ("CAPITAL_RAISE", (r"(stock|share|equity|debt) offering", r"convertible notes", r"capital raise"), 0.65, -1),
    ("BUYBACK", (r"buybacks?", r"share repurchases?", r"repurchase (authorization|program)"), 0.60, 1),
    ("MANAGEMENT_CHANGE", (r"ceo (resigns|departs|steps down)", r"cfo (resigns|departs|steps down)", r"(appoints|names) (new )?ceo", r"new ceo"), 0.60, 0),
    ("PRODUCT_EVENT", (r"launch(es|ed)?", r"approvals?", r"approved", r"clearance", r"recalls?"), 0.55, 0),
    ("CUSTOMER_CONTRACT", (r"contract win", r"wins (\w+ )?contract", r"partnership", r"customer deal", r"agreement with"), 0.55, 1),
    ("LAYOFF", (r"layoffs?", r"job cuts", r"cuts jobs", r"workforce reduction"), 0.50, 0),
)
# A regulator granting something is not an adverse regulatory action.
EVENT_EXCLUSIONS = {
    "REGULATORY_ACTION": re.compile(r"\b(approv\w*|clear(s|ed|ance)|authoriz\w*|grants?|granted)\b"),
}
NEGATIONS = {"no", "not", "never", "without", "denies", "deny", "denied", "avoids", "avoided", "dismissed"}
NEGATION_WINDOW = 3

TOKEN_RE = re.compile(r"[a-z0-9]+")
_COMPILED = tuple(
    (event, tuple(re.compile(r"\b" + p + r"\b") for p in phrases), materiality, direction)
    for event, phrases, materiality, direction in EVENT_RULES
)


def _tokens(title: str) -> set[str]:
    return set(TOKEN_RE.findall(title.lower()))


def _similarity(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def _negated(lowered: str, start: int) -> bool:
    before = TOKEN_RE.findall(lowered[:start])[-NEGATION_WINDOW:]
    return any(word in NEGATIONS for word in before)


def classify_title(title: str) -> dict:
    lowered = str(title or "").lower()
    hits = []
    for event, patterns, materiality, direction in _COMPILED:
        exclusion = EVENT_EXCLUSIONS.get(event)
        if exclusion and exclusion.search(lowered):
            continue
        matched = [
            m.group(0)
            for pattern in patterns
            for m in pattern.finditer(lowered)
            if not _negated(lowered, m.start())
        ]
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
