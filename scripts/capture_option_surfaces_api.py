"""Call the Vercel collector without loading database or IBKR credentials."""
import json
import os
import re
from urllib.parse import urlparse

import httpx


def main():
    base = os.getenv("STOCK_MACHINE_API_BASE_URL", "https://fundermental-stock-analysis.vercel.app").rstrip("/")
    token = os.getenv("STOCK_MACHINE_ADMIN_TOKEN", "")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise ValueError("API base must be an HTTPS origin without credentials")
    if len(token) < 24:
        raise ValueError("STOCK_MACHINE_ADMIN_TOKEN is missing or too short")
    tickers = list(dict.fromkeys(x.strip().upper() for x in os.getenv(
        "P1_OPTION_TICKERS", "AAPL,AMZN,GOOGL,META,MSFT,NVDA,TSLA,UBER,HIMS,SPY"
    ).split(",") if x.strip()))
    minimum = int(os.getenv("P1_OPTION_MIN_SUCCESSES", "8"))
    if not tickers or len(tickers) > 100 or not 1 <= minimum <= len(tickers):
        raise ValueError("invalid ticker count or minimum coverage")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", x) for x in tickers):
        raise ValueError("invalid ticker")
    successes = 0
    with httpx.Client(timeout=310, follow_redirects=False,
                      headers={"Authorization": f"Bearer {token}"}) as client:
        for ticker in tickers:
            result = {"ticker": ticker, "status": "error"}
            try:
                response = client.post(f"{base}/api/admin/options/{ticker}/capture")
                result["http_status"] = response.status_code
                if response.status_code == 200:
                    payload = response.json()
                    if (payload.get("status") == "ok" and payload.get("ticker") == ticker
                            and payload.get("snapshot_id") and payload.get("as_of")):
                        successes += 1
                        result.update(status="ok", snapshot_id=payload["snapshot_id"], as_of=payload["as_of"])
            except (httpx.HTTPError, ValueError):
                # Do not print exception URLs, headers, or arbitrary response bodies.
                result["reason"] = "request failed or invalid response"
            print(json.dumps(result), flush=True)
            if result.get("http_status") in (401, 403, 404):
                break
    passed = successes >= minimum
    print(json.dumps({"event": "summary", "status": "ok" if passed else "insufficient_coverage",
                      "successes": successes, "tickers_requested": len(tickers), "minimum_successes": minimum}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
