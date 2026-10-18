"""Fetch FMP's earnings-surprise history into a dated evidence file (no database).

  python scripts/fetch_fmp_surprise_history.py --probe        # 3 tickers, report only
  python scripts/fetch_fmp_surprise_history.py --out FILE     # all covered tickers

The strict point-in-time store (earnings_surprise_vintages) only knows a
surprise from when this system first observed it, so 2016-2025 history is
empty. FMP's /stable/earnings serves past announcements with the actual and
the estimate it recorded for each. This file labels them vendor history:
each event is usable from the session after its announcement date (the time
of day is unknown), and the estimate is FMP's record, not an observed
consensus vintage. The live agent never reads this file; only the replay does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.ingestion import estimates  # noqa: E402
from stock_machine.market_calendar import session_on_or_after, session_offset  # noqa: E402

LIMITS = (1000, 400, 100, 40, 5)
VERSION = "fmp-earnings-surprise-history.v1"
AVAILABILITY = "session after the announcement date (time of day unknown)"


def fetch(symbol: str) -> dict:
    """Largest history the plan allows for one symbol."""
    last_error = None
    for limit in LIMITS:
        payload, err = estimates._get("/stable/earnings", {"symbol": symbol, "limit": limit})
        if payload is not None:
            return {"limit": limit, "payload": payload}
        last_error = err
        if "limit" not in str((err or {}).get("detail", "")).lower() and (err or {}).get(
                "event") != "PROVIDER_PLAN_LIMIT":
            break
    return {"limit": None, "payload": None, "error": last_error}


def events(payload: list[dict], today: str) -> list[dict]:
    out = []
    for row in payload or []:
        day = str(row.get("date") or "")[:10]
        actual, estimate = row.get("epsActual"), row.get("epsEstimated")
        if not day or day >= today or actual is None or estimate is None or not estimate:
            continue
        available = session_offset(session_on_or_after(day), 1)
        out.append({"announcement_date": day, "available_session": available,
                    "actual_eps": actual, "estimated_eps": estimate,
                    "surprise_pct": round((actual - estimate) / abs(estimate) * 100, 4)})
    return sorted(out, key=lambda e: e["announcement_date"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true", help="three tickers; print coverage only")
    parser.add_argument("--out", default="data/vendor_history/fmp-earnings-surprises.json")
    args = parser.parse_args()
    if not estimates.FMP_API_KEY:
        raise SystemExit("FMP_API_KEY is required")
    tickers = ["AAPL", "MSFT", "F"] if args.probe else sorted(AGENT_UNIVERSE)
    today = datetime.now(timezone.utc).date().isoformat()
    result, coverage = {}, {}
    for ticker in tickers:
        got = fetch(ticker)
        rows = events(got.get("payload"), today)
        raw = json.dumps(got.get("payload"), sort_keys=True, default=str).encode()
        result[ticker] = {"limit_used": got["limit"], "raw_sha256": hashlib.sha256(raw).hexdigest(),
                          "error": (got.get("error") or {}).get("event"), "events": rows}
        coverage[ticker] = {"events": len(rows), "first": rows[0]["announcement_date"] if rows else None,
                            "last": rows[-1]["announcement_date"] if rows else None, "limit": got["limit"],
                            "error": (got.get("error") or {}).get("event")}
        print(ticker, json.dumps(coverage[ticker]), file=sys.stderr)
    if args.probe:
        print(json.dumps(coverage, indent=1))
        return 0
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": VERSION, "vendor": "FMP", "endpoint": "/stable/earnings",
                                "fetched_at": datetime.now(timezone.utc).isoformat(),
                                "availability": AVAILABILITY,
                                "estimate_basis": "vendor-recorded estimate for each announcement",
                                "tickers": result}, indent=1) + "\n")
    print(json.dumps({"tickers": len(result), "events": sum(c["events"] for c in coverage.values()),
                      "first": min((c["first"] for c in coverage.values() if c["first"]), default=None)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
