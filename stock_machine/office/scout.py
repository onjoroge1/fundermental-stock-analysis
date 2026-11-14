"""The scout: code only, no model. It turns new filings and company events into office events, once each,
and queues a bounded number of memo tasks for them."""
from __future__ import annotations

from . import config, tasks, tools

ROLE = "scout"
MEMO_EVENT_TYPES = ("EARNINGS", "FILING")
TASK_ANALYST = "analyst_memo"


def scan(*, lookback_days: int = 3, limit: int = 200) -> dict:
    """Record source rows from the last few days as office events. Repeating a scan adds nothing new."""
    config.require_enabled()
    with tasks.connect() as conn:
        candidates = (tools.source_events(ROLE, conn, lookback_days=lookback_days, limit=limit)
                      + tools.recent_filings(ROLE, conn, lookback_days=lookback_days, limit=limit))
        created = sum(tools.write_event(ROLE, conn, **c)["created"] for c in candidates)
    return {"seen": len(candidates), "created": created}


def queue_memos(*, limit: int = 5) -> list[dict]:
    """Queue one memo task for each of the newest events that has none. ``limit`` bounds the work per call;
    the spend caps bound it per day."""
    config.require_enabled()
    with tasks.connect() as conn:
        events = tools.events_awaiting_memo(ROLE, conn, event_types=MEMO_EVENT_TYPES, limit=limit)
    return [{"event_id": e["event_id"],
             **tasks.enqueue(f"memo:{e['event_id']}:v1", TASK_ANALYST, {"event_id": e["event_id"], "version": 1})}
            for e in events]
