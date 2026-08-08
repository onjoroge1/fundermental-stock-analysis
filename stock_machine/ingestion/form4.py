"""Form 4 insider-transaction ingestion from SEC EDGAR.

Classification follows the research consensus: discretionary open-market
purchases are the informative class; 10b5-1 planned trades, option
exercises, tax withholding, awards and gifts are routine noise. Raw XML is
preserved in immutable storage; ingestion is incremental (already-stored
accessions are skipped) so the daily refresh stays cheap after the first
pull."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..provenance import save_raw
from .sec import _get

TRANSACTION_LIMIT = 40  # most recent Form 4 filings per ticker per run


def _text(el, path: str) -> str | None:
    node = el.find(path)
    return node.text.strip() if node is not None and node.text else None


def classify(code: str | None, plan_10b5_1: bool) -> str:
    if code == "P":
        return "routine_planned" if plan_10b5_1 else "discretionary_purchase"
    if code == "S":
        return "routine_planned" if plan_10b5_1 else "discretionary_sale"
    if code in ("A", "F", "M", "G", "C", "D", "J", "W", "X"):
        return "routine_other"  # awards, tax, exercises, gifts, conversions
    return "other"


def parse_form4(xml_text: str) -> list[dict]:
    """One Form 4 → transaction rows. Malformed documents yield []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    plan_flag = (_text(root, ".//aff10b5One") or "").lower() in ("1", "true")
    owner = _text(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    role = None
    if rel is not None:
        if (_text(rel, "isOfficer") or "0") in ("1", "true"):
            role = _text(rel, "officerTitle") or "officer"
        elif (_text(rel, "isDirector") or "0") in ("1", "true"):
            role = "director"
        elif (_text(rel, "isTenPercentOwner") or "0") in ("1", "true"):
            role = "10% owner"
    rows = []
    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, ".//transactionCoding/transactionCode")
        shares = _text(tx, ".//transactionAmounts/transactionShares/value")
        price = _text(tx, ".//transactionAmounts/transactionPricePerShare/value")
        acquired = _text(
            tx, ".//transactionAmounts/transactionAcquiredDisposedCode/value")
        date = _text(tx, ".//transactionDate/value")
        try:
            shares_f = float(shares) if shares else None
            price_f = float(price) if price else None
        except ValueError:
            continue
        if not date or shares_f is None:
            continue
        rows.append({
            "transaction_date": date,
            "owner": owner,
            "role": role,
            "code": code,
            "acquired": acquired == "A",
            "shares": shares_f,
            "price": price_f,
            "value": (round(shares_f * price_f, 2)
                      if price_f is not None else None),
            "plan_10b5_1": plan_flag,
            "classification": classify(code, plan_flag),
        })
    return rows


def ingest_from_submissions(conn, ticker: str, cik: str,
                            submissions: dict,
                            limit: int = TRANSACTION_LIMIT) -> dict:
    """Fetch + parse recent Form 4s listed in the issuer's submissions.
    Incremental: accessions already stored are skipped."""
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = [
        (recent["accessionNumber"][i], recent["primaryDocument"][i],
         recent["filingDate"][i])
        for i, form in enumerate(recent.get("form", []))
        if form == "4"
    ][:limit]

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT accession FROM insider_transactions "
                    "WHERE ticker = %s", (ticker,))
        known = {r[0] for r in cur.fetchall()}

    cik_int = int(cik)
    fetched = inserted = errors = 0
    for accn, primary_doc, filed_at in accessions:
        if accn in known or not primary_doc:
            continue
        doc = primary_doc.split("/")[-1]  # strip xslF345X0x/ viewer prefix
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
               f"{accn.replace('-', '')}/{doc}")
        try:
            xml_text = _get(url).text
        except Exception:
            errors += 1
            continue
        fetched += 1
        save_raw("sec", [ticker, "form-4", accn], {"xml": xml_text}, url)
        rows = parse_form4(xml_text)
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO insider_transactions (ticker, accession,
                       filed_at, transaction_date, owner, role, code,
                       acquired, shares, price, value, plan_10b5_1,
                       classification)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (ticker, accn, filed_at, r["transaction_date"],
                     r["owner"], r["role"], r["code"], r["acquired"],
                     r["shares"], r["price"], r["value"], r["plan_10b5_1"],
                     r["classification"]))
                inserted += cur.rowcount
        conn.commit()
    return {"form4_listed": len(accessions), "form4_fetched": fetched,
            "form4_rows_inserted": inserted, "form4_errors": errors}


def insider_summary(conn, ticker: str, as_of: str, months: int = 6) -> dict:
    """Deterministic signal summary over the trailing window."""
    from datetime import date, timedelta
    start = (date.fromisoformat(as_of[:10])
             - timedelta(days=months * 30)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT classification, count(*), COALESCE(sum(value), 0),
                      count(DISTINCT owner)
               FROM insider_transactions
               WHERE ticker = %s AND transaction_date BETWEEN %s AND %s
               GROUP BY classification""",
            (ticker, start, as_of[:10]))
        agg = {c: {"n": n, "total_value": float(v), "owners": o}
               for c, n, v, o in cur.fetchall()}
        cur.execute(
            """SELECT transaction_date::text, owner, role, classification,
                      shares, price, value
               FROM insider_transactions
               WHERE ticker = %s AND transaction_date <= %s
               ORDER BY transaction_date DESC LIMIT 8""",
            (ticker, as_of[:10]))
        recent = [dict(zip(("date", "owner", "role", "classification",
                            "shares", "price", "value"), row))
                  for row in cur.fetchall()]

    buys = agg.get("discretionary_purchase", {"n": 0, "total_value": 0,
                                              "owners": 0})
    sells = agg.get("discretionary_sale", {"n": 0, "total_value": 0,
                                           "owners": 0})
    if buys["owners"] >= 2 and buys["total_value"] > sells["total_value"]:
        signal = "MULTIPLE_DISCRETIONARY_BUYERS"
    elif buys["n"] > 0 and buys["total_value"] > sells["total_value"]:
        signal = "NET_DISCRETIONARY_BUYING"
    elif sells["n"] > 0:
        signal = "NET_DISCRETIONARY_SELLING"
    elif agg:
        signal = "ROUTINE_ONLY"
    else:
        signal = "NO_DATA"
    return {
        "window_months": months,
        "signal": signal,
        "discretionary_purchases": buys,
        "discretionary_sales": sells,
        "routine": {k: v for k, v in agg.items()
                    if k.startswith("routine")},
        "recent_transactions": recent,
        "note": "Discretionary open-market purchases are the informative "
                "class; planned (10b5-1), award, tax and exercise "
                "transactions are classified routine. Sales carry weaker "
                "information than purchases.",
    }
