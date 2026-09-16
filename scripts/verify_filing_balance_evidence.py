"""Recheck the pinned filing audit against actual SEC inline XBRL elements.

Run with the audit environment's `lxml` installed. This verifies evidence only;
it never writes to production, qualifies debt, or changes guidance.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

import httpx


def verify(row, raw):
    from lxml import html
    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("SOURCE_CONTENT_CHANGED:" + row["ticker"])
    tree = html.fromstring(raw)
    elements = {x.get("id"): x for x in tree.iter() if x.get("id")}
    values = {}
    rounding = Decimal(0)
    for group, facts in row["evidence"].items():
        values[group] = Decimal(0)
        for fact in facts:
            el = elements[fact["element_id"]]
            if not str(el.tag).endswith(":nonfraction") or el.get("name") != fact["tag"]:
                raise ValueError("SOURCE_TAG_MISMATCH")
            if el.get("contextref") != fact["context_id"]:
                raise ValueError("SOURCE_CONTEXT_MISMATCH")
            context = elements[fact["context_id"]]
            instants = [x.text for x in context.iter() if str(x.tag).endswith(":instant")]
            entity = [x.text for x in context.iter() if str(x.tag).endswith(":identifier")]
            if instants != [row["period_end"]] or len(entity) != 1 or int(entity[0]) != int(row["cik"]):
                raise ValueError("SOURCE_ENTITY_OR_DATE_MISMATCH")
            if any(str(x.tag).endswith((":explicitmember", ":typedmember")) for x in context.iter()):
                raise ValueError("NONCONSOLIDATED_CONTEXT")
            unit = elements[el.get("unitref")]
            measures = [x.text for x in unit.iter() if str(x.tag).endswith(":measure")]
            if len(measures) != 1 or measures[0].split(":")[-1] != "USD":
                raise ValueError("NON_USD_UNIT")
            text = "".join(el.itertext()).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
            value = Decimal(0) if text in ("—", "–", "-") else Decimal(text)
            value *= Decimal(10) ** int(el.get("scale", "0"))
            if el.get("sign") == "-":
                value = -value
            if not value.is_finite() or value != Decimal(str(fact["value"])):
                raise ValueError("SOURCE_VALUE_MISMATCH")
            decimals = el.get("decimals")
            if decimals != fact["declared_decimals"] or el.get("scale") != fact["scale"]:
                raise ValueError("SOURCE_PRECISION_MISMATCH")
            rounding += Decimal(0) if decimals == "INF" else Decimal("0.5") * Decimal(10) ** (-int(decimals))
            values[group] += value
    residual = values["assets"] - values["liabilities"] - values["equity"] - values["temporary_equity"]
    expected = row["values"]
    if (values["assets"] != expected["assets"] or values["liabilities"] != expected["liabilities"]
            or values["equity"] != expected["equity_including_noncontrolling"]
            or values["temporary_equity"] != expected["temporary_equity"]
            or residual != row["identity_residual"] or rounding != row["declared_rounding_bound"]):
        raise ValueError("ARITHMETIC_OR_ROUNDING_MISMATCH")
    if abs(residual) > rounding:
        raise ValueError("BALANCE_IDENTITY_OUTSIDE_DECLARED_ROUNDING")
    return {"ticker": row["ticker"], "status": "SOURCE_AND_ARITHMETIC_VERIFIED", "residual": float(residual)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="Optional directory containing TICKER.html original SEC responses")
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parents[1] / "docs/evidence/latest-filing-balance-reconciliation-2026-09-16.json"
    audit = json.loads(evidence.read_text())
    assert len(audit["rows"]) == audit["expected"] == 54
    assert len({r["ticker"] for r in audit["rows"]}) == 54
    results = []
    with httpx.Client(timeout=45, headers={"User-Agent": "StockMachineAudit/1.0 public financial statement reconciliation"}) as client:
        for row in audit["rows"]:
            if args.source_dir:
                raw = (args.source_dir / (row["ticker"] + ".html")).read_bytes()
            else:
                if not re.fullmatch(r"https://www\.sec\.gov/Archives/edgar/data/\d+/\d+/[A-Za-z0-9_.-]+\.htm[l]?", row["source_url"]):
                    raise ValueError("UNEXPECTED_SOURCE_URL")
                response = client.get(row["source_url"])
                response.raise_for_status()
                raw = response.content
            results.append(verify(row, raw))
    print(json.dumps({"verified": len(results), "scope": "source elements and balance identity only", "trade_execution": False,
                      "rounding_residuals": [r for r in results if r["residual"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
