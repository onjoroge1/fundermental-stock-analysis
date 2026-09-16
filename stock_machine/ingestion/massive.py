"""Bounded Massive EOD verification and attributed news metadata.

This independent observation does not replace dividend-adjusted price history.
Contracts verified at massive.com/docs/rest/stocks/{aggregates/custom-bars,news}.
Credentials stay in Authorization headers, never URLs, records or errors.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..financial_integrity import number
from ..research_contract import digest, timestamp

BASE = "https://api.massive.com"
MAX_BYTES = 2_000_000


class MassiveUnavailable(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("MASSIVE_API_KEY") or os.getenv("MASSIVE_API"))


def _symbol(ticker):
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", ticker):
        raise ValueError("Invalid ticker")
    return ticker


def _get(path, params, *, client=None):
    key = os.getenv("MASSIVE_API_KEY") or os.getenv("MASSIVE_API")
    if not key:
        raise MassiveUnavailable("MASSIVE_NOT_CONFIGURED")
    owned = client is None
    client = client or httpx.Client(timeout=25, follow_redirects=False)
    try:
        response = client.get(BASE + path, params=params, headers={"Authorization": "Bearer " + key})
        if response.status_code != 200:
            raise MassiveUnavailable("MASSIVE_HTTP_" + str(response.status_code))
        if len(response.content) > MAX_BYTES:
            raise MassiveUnavailable("MASSIVE_RESPONSE_TOO_LARGE")
        value = response.json()
        if not isinstance(value, dict) or not isinstance(value.get("results"), list):
            raise MassiveUnavailable("MASSIVE_RESPONSE_SCHEMA_INVALID")
        if value.get("status") not in (None, "OK", "DELAYED"):
            raise MassiveUnavailable("MASSIVE_RESPONSE_NOT_OK")
        return value
    except MassiveUnavailable:
        raise
    except Exception:
        raise MassiveUnavailable("MASSIVE_REQUEST_FAILED") from None
    finally:
        if owned:
            client.close()


def daily_observation(ticker: str, market_date: str, *, client=None) -> dict:
    ticker = _symbol(ticker)
    end = date.fromisoformat(market_date)
    path = f"/v2/aggs/ticker/{ticker}/range/1/day/{end - timedelta(days=10)}/{end}"
    raw = _get(path, {"adjusted": "true", "sort": "asc", "limit": 20}, client=client)
    if raw.get("ticker") != ticker or raw.get("adjusted") is not True or raw.get("next_url") or len(raw["results"]) > 20:
        raise MassiveUnavailable("MASSIVE_IDENTITY_BASIS_OR_PAGINATION_INVALID")
    rows = []
    for r in raw["results"]:
        if not isinstance(r, dict) or not all(number(r.get(k)) is not None for k in ("o", "h", "l", "c", "v", "t")):
            raise MassiveUnavailable("MASSIVE_BAR_FIELDS_INVALID")
        try:
            day = datetime.fromtimestamp(r["t"] / 1000, timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        except (ValueError, OverflowError, OSError):
            raise MassiveUnavailable("MASSIVE_BAR_TIMESTAMP_INVALID") from None
        if not (end - timedelta(days=10)).isoformat() <= day <= market_date or r["l"] <= 0 or r["h"] < max(r["o"], r["c"], r["l"]) or r["l"] > min(r["o"], r["c"]) or r["v"] < 0:
            raise MassiveUnavailable("MASSIVE_BAR_INVALID_OR_FUTURE")
        rows.append({"date": day, "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]})
    if len({r["date"] for r in rows}) != len(rows) or rows != sorted(rows, key=lambda r: r["date"]) or not rows or rows[-1]["date"] != market_date:
        raise MassiveUnavailable("MASSIVE_LATEST_COMPLETED_SESSION_MISSING")
    return {"provider": "MASSIVE", "ticker": ticker, "market_date": market_date,
            "observed_at": datetime.now(timezone.utc).isoformat(), "status": "OBSERVED",
            "price_basis": "split-adjusted daily bars; not dividend-adjusted total return or intraday quote",
            "source_url": BASE + path, "source_content_sha256": digest(raw), "raw_response": raw, "rows": rows}


def news_observation(ticker: str, *, now=None, client=None) -> dict:
    ticker = _symbol(ticker)
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    raw = _get("/v2/reference/news", {"ticker": ticker, "published_utc.gte": start.isoformat(),
               "published_utc.lte": now.isoformat(), "sort": "published_utc", "order": "desc", "limit": 5}, client=client)
    if len(raw["results"]) > 5:
        raise MassiveUnavailable("MASSIVE_NEWS_LIMIT_EXCEEDED")
    articles = []
    for item in raw["results"]:
        if not isinstance(item, dict):
            raise MassiveUnavailable("MASSIVE_NEWS_SCHEMA_INVALID")
        published = timestamp(item.get("published_utc"))
        if (not published or not start <= published <= now or ticker not in item.get("tickers", [])
                or not str(item.get("article_url", "")).startswith("https://") or not item.get("id")):
            raise MassiveUnavailable("MASSIVE_NEWS_IDENTITY_OR_DATE_INVALID")
        articles.append({"source_id": "MASSIVE:NEWS:" + item["id"], "title": str(item.get("title", ""))[:500],
                         "published_utc": item["published_utc"], "source_url": item["article_url"],
                         "publisher": (item.get("publisher") or {}).get("name"),
                         "content_review": "UNREVIEWED_HEADLINE_METADATA"})
    return {"status": "OBSERVED" if articles else "NO_ARTICLES_RETURNED", "ticker": ticker,
            "provider": "MASSIVE", "observed_at": now.isoformat(), "articles": articles,
            "truncated": bool(raw.get("next_url")), "source_url": BASE + "/v2/reference/news",
            "source_content_sha256": digest(raw), "raw_response": raw,
            "limitation": "Metadata only; article contents and sentiment are not verified facts or instructions."}
