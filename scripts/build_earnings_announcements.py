"""Build the point-in-time earnings-announcement dataset from SEC 8-K item 2.02 filings.

  python scripts/build_earnings_announcements.py [--out docs/evidence/earnings-announcements-v1.json]

Reads data.sec.gov submissions (recent plus every paginated page reaching back
to 2015-06) for the covered universe and the predecessor registrants of DIS
and AVGO, then writes the events, their validation gates and the hash of every
SEC response. No database access. The event replay pins this file's sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from stock_machine.config import SEC_USER_AGENT  # noqa: E402
from stock_machine.events import earnings_announcements as ea  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/evidence/latest-filing-balance-reconciliation-2026-09-16.json"
REVIEWED = ROOT / "stock_machine/normalization/earnings_release_eps.json"


def get(client, url):
    for attempt in range(4):
        time.sleep(0.15)
        r = client.get(url)
        if r.status_code == 200:
            return r.content
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()


def submissions(client, cik: str):
    """All payloads for a registrant reaching back to ea.START, with their hashes."""
    first = get(client, f"https://data.sec.gov/submissions/CIK{cik}.json")
    data, sources = json.loads(first), [{"name": f"CIK{cik}.json", "sha256": hashlib.sha256(first).hexdigest()}]
    payloads = [data]
    for page in data.get("filings", {}).get("files", []):
        if page.get("filingTo", "9999") < ea.START:
            continue
        raw = get(client, f"https://data.sec.gov/submissions/{page['name']}")
        payloads.append(json.loads(raw))
        sources.append({"name": page["name"], "sha256": hashlib.sha256(raw).hexdigest()})
    return data.get("name", ""), payloads, sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "docs/evidence/earnings-announcements-v1.json"))
    parser.add_argument("--until", default=None, help="last acceptance date to include (default: today)")
    args = parser.parse_args()
    rows = json.loads(EVIDENCE.read_text())["rows"]
    events, sources, first_year = [], {}, {}
    with httpx.Client(timeout=60, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}) as client:
        for row in rows:
            ticker, cik = row["ticker"], row["cik"]
            name, payloads, src = submissions(client, cik)
            mine = ea.announcements(ticker, cik, ea.filings(payloads), until=args.until)
            sources[ticker] = {"cik": cik, "name": name, "pages": src}
            for pred_cik, must_contain, last in ea.PREDECESSORS.get(ticker, []):
                pred_name, pred_payloads, pred_src = submissions(client, pred_cik)
                if must_contain not in pred_name.upper():
                    raise SystemExit(f"{ticker}: predecessor {pred_cik} is '{pred_name}', expected {must_contain}")
                mine = ea.announcements(ticker, pred_cik, ea.filings(pred_payloads), until=last) + mine
                sources[ticker]["predecessor"] = {"cik": pred_cik, "name": pred_name, "until": last, "pages": pred_src}
            mine = ea.label_roles(mine)    # re-apply the cluster rule across the registrant change
            events.extend(mine)
            primary = [e for e in mine if e["role"] == "PRIMARY"]
            first_year[ticker] = int(primary[0]["reaction_session"][:4]) + 1 if primary else 2016
            print(ticker, len(mine), "events,", len(primary), "primary", file=sys.stderr)
    reviewed = {}
    for r in json.loads(REVIEWED.read_text()):
        reviewed.setdefault(r["ticker"], []).append(r["accession"])
    last_year = datetime.now(timezone.utc).year
    out = {"schema_version": ea.VERSION, "built_at": datetime.now(timezone.utc).isoformat(),
           "source": "data.sec.gov submissions; form 8-K with item 2.02; acceptanceDateTime (UTC)",
           "cluster_days": ea.CLUSTER_DAYS, "start": ea.START,
           "validation": ea.validate(events, reviewed_accessions=reviewed, first_year=first_year,
                                     last_year=last_year),
           "sources": sources, "events": events}
    path = Path(args.out)
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"events": len(events), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "validation": out["validation"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
