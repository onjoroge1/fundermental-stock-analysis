from datetime import datetime, timezone, timedelta
from stock_machine.agent_intelligence.news_events import build, classify_title


def test_guidance_cut_is_high_materiality_negative():
    row=classify_title("Company lowers guidance after weak demand")
    assert row["event_type"]=="GUIDANCE_CUT"
    assert row["direction"]==-1
    assert row["materiality"]>=.8


def test_build_applies_time_decay_and_duplicate_penalty():
    now=datetime(2026,9,19,12,tzinfo=timezone.utc)
    news={"status":"OBSERVED","articles":[
        {"source_id":"a","title":"Company raises guidance for full year","published_utc":(now-timedelta(hours=2)).isoformat()},
        {"source_id":"b","title":"Company raises guidance for the full year","published_utc":(now-timedelta(hours=3)).isoformat()},
    ]}
    value=build(news,now=now)
    assert value["status"]=="OK"
    assert value["events"][0]["weighted_materiality"] > value["events"][1]["weighted_materiality"]
    assert value["events"][1]["duplicate_like"] is True
    assert value["features"]["high_materiality_positive"] is True


def test_unavailable_news_fails_closed():
    value=build({"status":"UNAVAILABLE"})
    assert value["status"]=="UNAVAILABLE"
    assert value["events"]==[]


import pytest


@pytest.mark.parametrize("title,event,direction", [
    # Substring false positives from the v1 matcher.
    ("Acme issued strong third-quarter guidance", "OTHER", 0),
    ("FDA grants regulatory approval for Acme's lead drug", "PRODUCT_EVENT", 0),
    ("Regulators cleared the Acme merger", "M_AND_A", 0),
    ("Is Acme a stock to buy before earnings?", "OTHER", 0),
    ("Analyst: no warning signs at Acme", "OTHER", 0),
    ("Acme relaunches loyalty app", "OTHER", 0),
    # Negation within the preceding three words suppresses the event.
    ("Acme does not cut guidance despite tariffs", "OTHER", 0),
    ("Acme denies antitrust investigation report", "OTHER", 0),
    # True positives still classify.
    ("Acme sued by former distributor", "LITIGATION", -1),
    ("DOJ opens antitrust investigation into Acme", "REGULATORY_ACTION", -1),
    ("Acme issues profit warning", "GUIDANCE_CUT", -1),
    ("Acme agrees to buy Widget Co for $2 billion", "M_AND_A", 0),
    ("Acme launches new platform", "PRODUCT_EVENT", 0),
    ("Acme beats estimates and raises full-year outlook", "GUIDANCE_RAISE", 1),
])
def test_word_boundary_negation_and_approval_rules(title, event, direction):
    row = classify_title(title)
    assert (row["event_type"], row["direction"]) == (event, direction)


def test_false_positive_headlines_no_longer_block_paper_trades():
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    titles = ["Acme issued strong guidance", "FDA grants regulatory approval to Acme",
              "Analyst sees no warning signs at Acme"]
    news = {"status": "OBSERVED", "articles": [
        {"source_id": str(i), "title": t, "published_utc": (now - timedelta(hours=1)).isoformat()}
        for i, t in enumerate(titles)]}
    assert build(news, now=now)["features"]["high_materiality_negative"] is False
