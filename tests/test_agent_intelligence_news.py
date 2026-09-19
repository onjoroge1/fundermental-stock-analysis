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
