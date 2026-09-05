"""Exchange-session dates shared by health, forecasts and outcome scoring.

XNYS supplies holidays, exceptional closures and early closes. An explicit
date means the information set at the start of that local day; a datetime
can admit that day's bar only after its actual exchange close.
"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


@lru_cache(maxsize=16)
def _calendar(first_year: int, last_year: int):
    import exchange_calendars
    return exchange_calendars.get_calendar(
        "XNYS", start=f"{first_year - 1}-01-01", end=f"{last_year + 1}-12-31")


def market_now() -> datetime:
    return datetime.now(EASTERN)


def latest_completed_session(as_of: date | datetime | None = None) -> str:
    now = as_of or market_now()
    if not isinstance(now, datetime):
        now = datetime.combine(now, time.min, tzinfo=EASTERN)
    if now.tzinfo is None:
        raise ValueError("market timestamp must have a timezone")
    now = now.astimezone(EASTERN)
    cal = _calendar(now.year, now.year)
    session = cal.date_to_session(now.date().isoformat(), direction="previous")
    if cal.session_close(session).to_pydatetime() > now:
        session = cal.previous_session(session)
    return session.date().isoformat()


def session_on_or_before(value: str) -> str:
    d = date.fromisoformat(value[:10])
    return _calendar(d.year, d.year).date_to_session(d.isoformat(), direction="previous").date().isoformat()


def session_on_or_after(value: str) -> str:
    d = date.fromisoformat(value[:10])
    return _calendar(d.year, d.year).date_to_session(d.isoformat(), direction="next").date().isoformat()


def sessions_between(start: str, end: str) -> int:
    a, b = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    if a >= b:
        return 0
    cal = _calendar(a.year, b.year)
    return len(cal.sessions_in_range((a + timedelta(days=1)).isoformat(), b.isoformat()))


def session_offset(origin: str, sessions: int) -> str:
    """Exact exchange-session target; missing vendor bars never extend a horizon."""
    d = date.fromisoformat(origin[:10])
    cal = _calendar(d.year, d.year + max(1, sessions // 200 + 1))
    start = cal.date_to_session(d.isoformat(), direction="previous")
    return cal.session_offset(start, sessions).date().isoformat()


def calendar_dte_to_sessions(as_of: date, calendar_days: int) -> int:
    return sessions_between(as_of.isoformat(), (as_of + timedelta(days=calendar_days)).isoformat())


def price_freshness(latest: str | None, *, as_of: date | datetime | None = None) -> dict:
    expected = latest_completed_session(as_of)
    latest = str(latest)[:10] if latest else None
    if not latest:
        return {"status": "MISSING", "latest_market_date": None,
                "expected_market_date": expected, "missing_sessions": None}
    if latest > expected:
        # A live, incomplete bar must not be confused with a completed close.
        status = "INCOMPLETE"
    else:
        status = "CURRENT" if latest == expected else "STALE"
    return {"status": status, "latest_market_date": latest,
            "expected_market_date": expected,
            "missing_sessions": sessions_between(latest, expected)}
