"""Invalidation monitoring: the machine-checkable subset of each report's
invalidation conditions, evaluated against fresh bundle data every refresh.

Honesty rules:
- Rules encode the QUANTIFIABLE conditions written in the analyst reports;
  prose-only conditions (guidance cuts, customer losses) stay human-checked.
- A breach FLAGS the thesis and any paper position — it never auto-closes or
  rewrites anything. The analyst pass decides; the machine surfaces.
- Breaches are stored once per (ticker, rule, report) — idempotent.
- GENERIC_RULES apply to every covered name and are labeled as monitoring
  heuristics, not report conditions."""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS sm_invalidation_events (
    ticker TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    observed DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    op TEXT,
    description TEXT,
    triggered_at DATE DEFAULT CURRENT_DATE,
    PRIMARY KEY (ticker, rule_id, report_id)
);
"""


def R(rule_id, metric, op, threshold, description):
    return {"rule_id": rule_id, "metric": metric, "op": op,
            "threshold": threshold, "description": description}


# per-ticker rules transcribed from each report's invalidation_conditions
RULES: dict[str, list[dict]] = {
    "UBER": [R("rev_lt_8", "growth.revenue_yoy_pct", "lt", 8,
               "Revenue growth below 8% (report invalidation)"),
             R("fcfm_lt_12", "profitability.fcf_margin_pct", "lt", 12,
               "FCF margin below 12% (report invalidation)")],
    "ADBE": [R("rev_lt_8", "growth.revenue_yoy_pct", "lt", 8,
               "Revenue growth below 8%"),
             R("fcfm_lt_35", "profitability.fcf_margin_pct", "lt", 35,
               "FCF margin below 35%")],
    "CRM": [R("rev_lt_8", "growth.revenue_yoy_pct", "lt", 8,
              "Revenue growth below 8%"),
            R("om_lt_20", "profitability.operating_margin_pct", "lt", 20,
              "Operating margin expansion stalled below 20%"),
            R("recgap_gt_10",
              "earnings_quality.receivables_growth_minus_revenue_growth_pct",
              "gt", 10, "Receivables-revenue gap above 10pt")],
    "NKE": [R("recgap_gt_20",
              "earnings_quality.receivables_growth_minus_revenue_growth_pct",
              "gt", 20, "Receivables-revenue gap above 20pt again"),
            R("gm_lt_40", "profitability.gross_margin_pct", "lt", 40,
              "Gross margin below 40%")],
    "TSLA": [R("om_gt_10", "profitability.operating_margin_pct", "gt", 10,
               "Operating margin above 10% INVALIDATES the deterioration "
               "premise (thesis-positive breach)")],
    "GOOGL": [R("oi_lt_10", "growth.operating_income_yoy_pct", "lt", 10,
                "Operating income growth below 10%")],
    "AMZN": [R("oi_lt_10", "growth.operating_income_yoy_pct", "lt", 10,
               "Operating income growth below 10%"),
             R("recgap_gt_20",
               "earnings_quality.receivables_growth_minus_revenue_growth_pct",
               "gt", 20, "Receivables-revenue gap above 20pt")],
    "GM": [R("fcfm_lt_5", "profitability.fcf_margin_pct", "lt", 5,
             "FCF margin below 5%")],
    "F": [R("fcf_lt_6b", "ttm.free_cash_flow", "lt", 6e9,
            "TTM FCF below $6B")],
    "BKNG": [R("rev_lt_8", "growth.revenue_yoy_pct", "lt", 8,
               "Revenue growth below 8%")],
    "IBM": [R("fcf_lt_11b", "ttm.free_cash_flow", "lt", 11e9,
              "TTM FCF below $11B")],
    "INTU": [R("rev_lt_7", "growth.revenue_yoy_pct", "lt", 7,
               "Revenue growth below 7%"),
             R("sbc_gt_12", "earnings_quality.stock_comp_to_revenue_pct",
               "gt", 12, "SBC above 12% of revenue")],
    "NOW": [R("rev_lt_18", "growth.revenue_yoy_pct", "lt", 18,
              "Revenue growth below 18%"),
            R("sbc_gt_16", "earnings_quality.stock_comp_to_revenue_pct",
              "gt", 16, "SBC above 16% of revenue")],
    "NFLX": [R("rev_lt_9", "growth.revenue_yoy_pct", "lt", 9,
               "Revenue growth below 9%"),
             R("om_lt_25", "profitability.operating_margin_pct", "lt", 25,
               "Operating margin below 25%")],
    "ORCL": [R("fcf_lt_neg30b", "ttm.free_cash_flow", "lt", -30e9,
               "TTM FCF worse than -$30B")],
    "PLTR": [R("rev_lt_40", "growth.revenue_yoy_pct", "lt", 40,
               "Revenue growth below 40%"),
             R("sbc_gt_16", "earnings_quality.stock_comp_to_revenue_pct",
               "gt", 16, "SBC above 16% of revenue")],
    "ABNB": [R("rev_lt_12", "growth.revenue_yoy_pct", "lt", 12,
               "Revenue growth below 12%")],
    "CMG": [R("oi_lt_0", "growth.operating_income_yoy_pct", "lt", 0,
              "Operating income growth still negative")],
    "WMT": [R("fcfm_lt_1_5", "profitability.fcf_margin_pct", "lt", 1.5,
              "FCF margin below 1.5%")],
    "HD": [R("recgap_gt_15",
             "earnings_quality.receivables_growth_minus_revenue_growth_pct",
             "gt", 15, "Receivables gap above 15pt")],
    "TGT": [R("oi_lt_0", "growth.operating_income_yoy_pct", "lt", 0,
              "Operating income decline continuing")],
    "AMD": [R("gm_lt_45", "profitability.gross_margin_pct", "lt", 45,
              "Gross margin below 45%")],
    "AVGO": [R("recgap_gt_30",
               "earnings_quality.receivables_growth_minus_revenue_growth_pct",
               "gt", 30, "Receivables gap above 30pt again"),
             R("fcfm_lt_38", "profitability.fcf_margin_pct", "lt", 38,
               "FCF margin below 38%")],
    "MU": [R("recgap_gt_25",
             "earnings_quality.receivables_growth_minus_revenue_growth_pct",
             "gt", 25, "Receivables gap persisting above 25pt")],
    "INTC": [R("gm_lt_35", "profitability.gross_margin_pct", "lt", 35,
               "Gross margin below 35%")],
    "TXN": [R("gm_lt_55", "profitability.gross_margin_pct", "lt", 55,
              "Gross margin below 55%")],
    "ADI": [R("recgap_gt_15",
              "earnings_quality.receivables_growth_minus_revenue_growth_pct",
              "gt", 15, "Receivables gap above 15pt"),
            R("gm_lt_60", "profitability.gross_margin_pct", "lt", 60,
              "Gross margin below 60%")],
    "CSCO": [R("recgap_gt_15",
               "earnings_quality.receivables_growth_minus_revenue_growth_pct",
               "gt", 15, "Receivables gap above 15pt"),
             R("rev_lt_6", "growth.revenue_yoy_pct", "lt", 6,
               "Revenue growth below 6%")],
    "DELL": [R("recgap_gt_40",
               "earnings_quality.receivables_growth_minus_revenue_growth_pct",
               "gt", 40, "Receivables gap above 40pt again")],
    "HPQ": [R("recgap_gt_20",
              "earnings_quality.receivables_growth_minus_revenue_growth_pct",
              "gt", 20, "Receivables gap above 20pt again"),
            R("fcf_lt_3b", "ttm.free_cash_flow", "lt", 3e9,
              "TTM FCF below $3B")],
    "RIVN": [R("gm_lt_0", "profitability.gross_margin_pct", "lt", 0,
               "Gross margin back below zero")],
    "SOFI": [R("rev_lt_20", "growth.revenue_yoy_pct", "lt", 20,
               "Revenue growth below 20%"),
             R("deposits_lt_15", "bank.deposits_yoy_pct", "lt", 15,
               "Deposit growth below 15%/yr"),
             R("provisions_gt_3", "bank.provisions_to_revenue_pct", "gt", 3,
               "Provisions above 3% of revenue — credit normalization")],
    "T": [R("fcf_lt_14b", "ttm.free_cash_flow", "lt", 14e9,
            "TTM FCF below $14B")],
    "TMUS": [R("fcfm_lt_16", "profitability.fcf_margin_pct", "lt", 16,
               "FCF margin below 16%")],
    "CMCSA": [R("fcf_lt_16b", "ttm.free_cash_flow", "lt", 16e9,
                "TTM FCF below $16B"),
              R("oi_lt_neg15", "growth.operating_income_yoy_pct", "lt", -15,
                "OI decline worse than -15% again")],
    "LUV": [R("recgap_gt_20",
              "earnings_quality.receivables_growth_minus_revenue_growth_pct",
              "gt", 20, "Receivables gap above 20pt"),
            R("om_lt_2", "profitability.operating_margin_pct", "lt", 2,
              "Operating margin below 2%")],
    "AAL": [R("om_lt_1", "profitability.operating_margin_pct", "lt", 1,
              "Operating margin below 1%")],
    "DAL": [R("om_lt_6", "profitability.operating_margin_pct", "lt", 6,
              "Operating margin below 6%")],
    "UAL": [R("om_lt_5", "profitability.operating_margin_pct", "lt", 5,
              "Operating margin below 5%")],
    "DIS": [R("lev_note", "profitability.operating_margin_pct", "lt", 12,
              "Operating margin below 12% — parks/streaming squeeze")],
    "KLAC": [R("fcfm_lt_25", "profitability.fcf_margin_pct", "lt", 25,
               "FCF margin below 25%")],
}

# monitoring heuristics applied to every name with an open report —
# labeled as such, distinct from report-transcribed conditions
GENERIC_RULES = [
    R("generic_recgap_gt_25",
      "earnings_quality.receivables_growth_minus_revenue_growth_pct",
      "gt", 25, "[generic heuristic] receivables outgrowing revenue by >25pt"),
]


def _resolve(bundle: dict, metric: str):
    if metric.startswith("ttm."):
        ttm = bundle.get("financial_history", {}).get("ttm") or {}
        return (ttm.get("fields") or {}).get(metric[4:])
    group, _, name = metric.partition(".")
    return (bundle.get("derived_metrics", {}).get(group) or {}).get(name)


def check_bundle(bundle: dict) -> list[dict]:
    """Evaluate all rules for one ticker's fresh bundle. Pure function."""
    ticker = bundle["company"]["ticker"]
    breaches = []
    seen_rule_ids = set()
    for rule in RULES.get(ticker, []) + GENERIC_RULES:
        if rule["rule_id"] in seen_rule_ids:
            continue
        seen_rule_ids.add(rule["rule_id"])
        observed = _resolve(bundle, rule["metric"])
        if observed is None:
            continue  # missing data is a data-quality issue, not a breach
        hit = (observed < rule["threshold"] if rule["op"] == "lt"
               else observed > rule["threshold"])
        if hit:
            breaches.append({**rule, "observed": round(float(observed), 2),
                             "ticker": ticker})
    return breaches


def record_breaches(conn, ticker: str, report_id: str,
                    breaches: list[dict]) -> list[dict]:
    """Store new breaches; returns only the ones not previously recorded."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        new = []
        for b in breaches:
            cur.execute(
                """INSERT INTO sm_invalidation_events (ticker, rule_id,
                   report_id, observed, threshold, op, description)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (ticker, b["rule_id"], report_id, b["observed"],
                 b["threshold"], b["op"], b["description"]))
            if cur.rowcount:
                new.append(b)
    conn.commit()
    return new


def active_breaches(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute(
            """SELECT rule_id, report_id, observed, threshold, op,
                      description, triggered_at::text
               FROM sm_invalidation_events WHERE ticker = %s
               ORDER BY triggered_at DESC""", (ticker,))
        cols = ["rule_id", "report_id", "observed", "threshold", "op",
                "description", "triggered_at"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
