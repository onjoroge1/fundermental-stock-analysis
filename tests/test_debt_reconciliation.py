"""Debt drafts read face balance sheet lines and never qualify debt on their own."""

from stock_machine import debt_reconciliation as dr
from stock_machine import financial_integrity as fi

CIK = "0000000042"
END = "2026-06-30"


def fact(tag, millions, ctx="c-1", ident=None):
    ident = ident or tag.split(":")[-1]
    return (f'<ix:nonfraction name="{tag}" contextref="{ctx}" unitref="usd" decimals="-6" '
            f'scale="6" id="{ident}" format="ixt:num-dot-decimal">{millions:,}</ix:nonfraction>')


def row(label, *facts):
    return f"<tr><td>{label}</td>" + "".join(f"<td>{f}</td>" for f in facts) + "</tr>"


def filing(face_rows, note_rows=()):
    header = f"""<ix:header><ix:resources>
      <xbrli:context id="c-1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:instant>{END}</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="c-seg"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier>
        <xbrli:segment><xbrldi:explicitmember dimension="a">b</xbrldi:explicitmember></xbrli:segment></xbrli:entity>
        <xbrli:period><xbrli:instant>{END}</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
    </ix:resources></ix:header>"""
    return ("<html><body>" + header + "<table>" + "".join(face_rows) + "</table>"
            + "<table>" + "".join(note_rows) + "</table></body></html>")


BASE = [
    row("Cash and cash equivalents", fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", 500)),
    row("Total assets", fact("us-gaap:Assets", 10_000)),
    row("Short-term debt", fact("us-gaap:DebtCurrent", 1_000)),
    row("Long-term debt", fact("us-gaap:LongTermDebtNoncurrent", 3_000)),
    row("Operating lease liabilities", fact("us-gaap:OperatingLeaseLiabilityNoncurrent", 700)),
    row("Total liabilities", fact("us-gaap:Liabilities", 6_000)),
    row("Total stockholders' equity", fact("us-gaap:StockholdersEquity", 4_000)),
    row("Total liabilities and equity", fact("us-gaap:LiabilitiesAndStockholdersEquity", 10_000, ident="lse")),
]


def facts(face, notes=()):
    return dr.consolidated_facts(filing(face, notes), cik=CIK, period_end=END)


def period(accession="A-1", **override):
    fields = {"total_assets": 10_000e6, "total_liabilities": 6_000e6, "shareholders_equity": 4_000e6,
              "cash_and_equivalents": 500e6, **override}
    fields = {k: int(v) for k, v in fields.items() if v is not None}
    return {"period_end": END, "fields": fields, "field_sources": {k: accession for k in fields}}


def test_face_debt_lines_are_proposed_and_corroborated_by_a_note_total():
    found = facts(BASE, [row("Total debt", fact("us-gaap:LongTermDebt", 4_000, ident="note"))])
    proposal = dr.propose(found)
    assert proposal["status"] == dr.DRAFT_CLEAN
    assert proposal["total_debt"] == 4_000_000_000
    assert [c["tag"] for c in proposal["components"]] == ["us-gaap:DebtCurrent", "us-gaap:LongTermDebtNoncurrent"]
    assert proposal["components"][0]["label"] == "Short-term debt"
    assert proposal["corroborated_by"][0]["element_id"] == "note"


def test_operating_leases_and_dimensional_facts_are_never_debt():
    face = BASE + [row("Segment debt", fact("us-gaap:LongTermDebtNoncurrent", 999, ctx="c-seg", ident="seg"))]
    proposal = dr.propose(facts(face))
    assert proposal["total_debt"] == 4_000_000_000
    assert "NOT_CORROBORATED_BY_A_DEBT_NOTE_TOTAL" in proposal["flags"]
    assert proposal["status"] == dr.DRAFT_NEEDS_REVIEW


def test_a_face_subtotal_is_not_double_counted():
    face = BASE[:4] + [row("Total debt", fact("us-gaap:LongTermDebt", 4_000, ident="sub"))] + BASE[4:]
    proposal = dr.propose(facts(face))
    assert proposal["total_debt"] == 4_000_000_000
    assert "FACE_SUBTOTAL_EXCLUDED:us-gaap:LongTermDebt" in proposal["flags"]


def test_missing_face_debt_is_no_proposal_never_zero():
    face = [r for r in BASE if "debt" not in r]
    proposal = dr.propose(facts(face))
    assert proposal["status"] == dr.NO_PROPOSAL and proposal["total_debt"] is None


def test_extension_tags_and_finance_leases_need_review():
    face = BASE[:2] + [row("Debt and finance leases", fact("acme:DebtAndFinanceLeasesCurrent", 1_000))] + BASE[3:]
    flags = dr.propose(facts(face))["flags"]
    assert "ISSUER_EXTENSION_TAGS:acme:DebtAndFinanceLeasesCurrent" in flags
    assert "FINANCE_LEASES_INCLUDED" in flags


def test_promoted_entry_is_accepted_by_financial_integrity_only_with_matching_bindings():
    found = facts(BASE, [row("Total debt", fact("us-gaap:LongTermDebt", 4_000, ident="note"))])
    bound, flags = dr.bindings(found, period(), "A-1")
    assert set(bound) == {"total_assets", "total_liabilities", "shareholders_equity", "cash_and_equivalents"}
    assert flags == []
    draft = {"ticker": "ACME", "cik": CIK, "period_end": END, "accession_number": "A-1",
             "source_url": "https://www.sec.gov/x.htm", "source_sha256": "0" * 64,
             "proposal": dr.propose(found), "binding_fields": bound}
    entry = dr.reviewed_entry(draft, reviewed_on="2026-10-17", note="test")
    p = period()
    assert fi.validate_reviewed_debt(entry, p["fields"], p["field_sources"])[0] == 4_000_000_000
    # A different filing or a changed normalized value voids the review.
    other = period(accession="A-2")
    assert fi.validate_reviewed_debt(entry, other["fields"], other["field_sources"])[0] is None
    changed = period(total_assets=10_001e6)
    assert fi.validate_reviewed_debt(entry, changed["fields"], changed["field_sources"])[0] is None
    # A draft (unpromoted status) never qualifies.
    assert fi.validate_reviewed_debt({**entry, "status": dr.DRAFT_CLEAN}, p["fields"], p["field_sources"])[0] is None


def test_bindings_skip_fields_from_another_filing_or_with_different_values():
    found = facts(BASE)
    p = period(cash_and_equivalents=None)
    p["field_sources"]["total_assets"] = "A-9"
    p["fields"]["shareholders_equity"] = 1
    bound, flags = dr.bindings(found, p, "A-1")
    assert set(bound) == {"total_liabilities"}
    assert "NOT_NORMALIZED_FROM_THIS_FILING:total_assets" in flags
    assert "NOT_NORMALIZED_FROM_THIS_FILING:cash_and_equivalents" in flags
    assert "NORMALIZED_VALUE_DIFFERS_FROM_FACE:shareholders_equity" in flags
