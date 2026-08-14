"""Interactive Brokers Flex Web Service ingestion.

Purpose: pull the user's ACTUAL account data (positions, trades, cash) so the
real portfolio can sit alongside the paper book. Flex is a reporting API —
it serves account statements, not market data.

Auth model (IBKR's design, not ours): a Flex TOKEN authorizes access, and a
FLEX QUERY ID (created by the user in Client Portal → Performance & Reports
→ Flex Queries) defines what a statement contains. Both are required:
  1. SendRequest?t=TOKEN&q=QUERY_ID&v=3  -> reference code
  2. GetStatement?t=TOKEN&q=REF_CODE&v=3 -> XML statement (poll until ready)

Without a query ID the module degrades to a clear data-quality event —
never a guess. Raw XML statements are preserved in immutable storage."""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET

import httpx

from ..provenance import save_raw

BASE = ("https://ndcdyn.interactivebrokers.com/AccountManagement"
        "/FlexWebService")
_UA = {"User-Agent": "stock-machine flex client"}

IBKR_FLEX_TOKEN = os.environ.get("IBKR_FLEX_TOKEN")
IBKR_FLEX_QUERY_ID = os.environ.get("IBKR_FLEX_QUERY_ID")

MISSING_QUERY_EVENT = {
    "event": "MISSING_CONFIG",
    "dataset": "ibkr_flex",
    "detail": "IBKR_FLEX_QUERY_ID unset. A Flex token alone cannot fetch "
              "data — create a Flex Query (Client Portal → Performance & "
              "Reports → Flex Queries) covering Open Positions, Trades and "
              "Cash Report, then put its ID in .env.",
}


def _status(root: ET.Element) -> tuple[str | None, str | None]:
    status = root.findtext("Status")
    code = root.findtext("ErrorCode")
    msg = root.findtext("ErrorMessage")
    return status, (f"{code}: {msg}" if code or msg else None)


def probe_token() -> dict:
    """Verify what the configured token can do WITHOUT a query ID — used to
    give the user a precise status instead of a shrug."""
    if not IBKR_FLEX_TOKEN:
        return {"ok": False, "detail": "IBKR_FLEX_TOKEN unset"}
    resp = httpx.get(f"{BASE}/SendRequest",
                     params={"t": IBKR_FLEX_TOKEN, "q": "0", "v": "3"},
                     headers=_UA, timeout=30)
    try:
        root = ET.fromstring(resp.text)
        status, err = _status(root)
        return {"ok": status == "Success", "http": resp.status_code,
                "status": status, "error": err}
    except ET.ParseError:
        return {"ok": False, "http": resp.status_code,
                "error": f"non-XML response: {resp.text[:160]}"}


def fetch_statement(query_id: str | None = None,
                    max_wait_s: int = 60) -> dict:
    """Full two-step Flex fetch. Returns {status, positions, trades, cash,
    events}."""
    query_id = query_id or IBKR_FLEX_QUERY_ID
    if not IBKR_FLEX_TOKEN:
        return {"status": "NOT_CONFIGURED", "events": [
            {"event": "MISSING_CONFIG", "dataset": "ibkr_flex",
             "detail": "IBKR_FLEX_TOKEN unset"}]}
    if not query_id:
        return {"status": "NOT_CONFIGURED", "events": [MISSING_QUERY_EVENT]}

    resp = httpx.get(f"{BASE}/SendRequest",
                     params={"t": IBKR_FLEX_TOKEN, "q": query_id, "v": "3"},
                     headers=_UA, timeout=30)
    root = ET.fromstring(resp.text)
    status, err = _status(root)
    if status != "Success":
        return {"status": "ERROR", "events": [
            {"event": "PROVIDER_ERROR", "dataset": "ibkr_flex",
             "detail": f"SendRequest failed — {err or status}"}]}
    ref_code = root.findtext("ReferenceCode")

    # poll GetStatement — IBKR generates asynchronously
    deadline = time.monotonic() + max_wait_s
    while True:
        resp = httpx.get(f"{BASE}/GetStatement",
                         params={"t": IBKR_FLEX_TOKEN, "q": ref_code,
                                 "v": "3"},
                         headers=_UA, timeout=60)
        if resp.text.lstrip().startswith("<FlexQueryResponse"):
            break
        root = ET.fromstring(resp.text)
        _, err = _status(root)
        if err and "1019" not in err:  # 1019 = statement not yet ready
            return {"status": "ERROR", "events": [
                {"event": "PROVIDER_ERROR", "dataset": "ibkr_flex",
                 "detail": f"GetStatement failed — {err}"}]}
        if time.monotonic() > deadline:
            return {"status": "TIMEOUT", "events": [
                {"event": "PROVIDER_ERROR", "dataset": "ibkr_flex",
                 "detail": f"statement not ready within {max_wait_s}s"}]}
        time.sleep(3)

    save_raw("ibkr", ["flex_statements"], {"xml": resp.text},
             f"{BASE}/GetStatement?q={ref_code}")
    stmt = ET.fromstring(resp.text)

    def rows(tag: str) -> list[dict]:
        return [dict(el.attrib) for el in stmt.iter(tag)]

    return {
        "status": "OK",
        "positions": rows("OpenPosition"),
        "trades": rows("Trade"),
        "cash": rows("CashReportCurrency"),
        "accounts": sorted({el.get("accountId") for el in
                            stmt.iter() if el.get("accountId")}),
        "events": [],
    }
