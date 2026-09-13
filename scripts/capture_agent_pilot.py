"""Manually capture the pilot via the deployed, admin-authenticated API.

No schedule is installed. Reuse --run-id after any ambiguous timeout.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

import httpx

from stock_machine.agents.contracts import CaptureRequest, PILOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Stable idempotency key; reuse on retry")
    parser.add_argument("--ticker", choices=PILOT, help="Omit to capture all five sequentially")
    args = parser.parse_args()
    request = CaptureRequest(idempotency_key=args.run_id)
    origin = os.environ.get("STOCK_MACHINE_API_BASE_URL", "").rstrip("/")
    parsed = urlparse(origin)
    token = os.environ.get("STOCK_MACHINE_ADMIN_TOKEN", "")
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or len(token) < 24:
        print("Configure an HTTPS STOCK_MACHINE_API_BASE_URL origin and the existing admin token.", file=sys.stderr)
        return 2
    failed = False
    with httpx.Client(timeout=310, follow_redirects=False) as client:
        for ticker in ([args.ticker] if args.ticker else PILOT):
            try:
                response = client.post(f"{origin}/api/admin/agents/{ticker}/capture",
                    headers={"Authorization": f"Bearer {token}"}, json=request.model_dump())
                response.raise_for_status()
                result = response.json()
                decision = result["decision"]
                print(f"{ticker}: {decision['status']} {decision['decision_id']} replayed={result['replayed']}")
                failed = failed or decision["status"] == "FAILED"
            except Exception:
                # Never print headers, provider responses, credentials or redirects.
                print(f"{ticker}: capture not confirmed; inspect journal and retry the same --run-id", file=sys.stderr)
                return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
