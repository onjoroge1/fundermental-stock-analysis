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


def test_notes_and_loans_tags_are_debt():
    # Oracle reports its non-current notes as LongTermNotesAndLoans.
    assert dr.is_debt_tag("us-gaap:LongTermNotesAndLoans")
    assert not dr.is_debt_tag("us-gaap:NotesAndLoansReceivableNetCurrent")


def test_face_debt_in_a_dimensional_context_blocks_the_proposal():
    # T-Mobile tags its long-term debt line by related party: summing only the
    # consolidated lines would understate debt by most of the balance.
    face = BASE + [row("Long-term debt", fact("us-gaap:LongTermDebtNoncurrent", 9_000, ctx="c-seg", ident="dim"))]
    raw = filing(face)
    proposal = dr.propose(dr.consolidated_facts(raw, cik=CIK, period_end=END),
                          dr.consolidated_facts(raw, cik=CIK, period_end=END, dimensional=True))
    assert proposal["status"] == dr.NO_PROPOSAL and proposal["total_debt"] is None
    assert "FACE_DEBT_LINES_IN_DIMENSIONAL_CONTEXTS" in proposal["flags"]
    assert proposal["dimensional_face_debt"][0]["value"] == 9_000_000_000


# ---- missing total liabilities / cash ----

NO_TOTAL = [
    row("Cash and cash equivalents", fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", 500)),
    row("Total assets", fact("us-gaap:Assets", 10_000)),
    row("Total current liabilities", fact("us-gaap:LiabilitiesCurrent", 2_000)),
    row("Long-term debt", fact("us-gaap:LongTermDebtNoncurrent", 3_000)),
    row("Deferred taxes", fact("us-gaap:DeferredIncomeTaxLiabilitiesNet", 400)),
    row("Other liabilities", fact("us-gaap:OtherLiabilitiesNoncurrent", 600)),
    row("Commitments and contingencies", ""),
    row("Common stock", fact("us-gaap:CommonStockValue", 100)),
    row("Total stockholders' equity", fact("us-gaap:StockholdersEquity", 4_000)),
    row("Total liabilities and equity", fact("us-gaap:LiabilitiesAndStockholdersEquity", 10_000, ident="lse")),
]


def balance_period(**override):
    fields = {"total_assets": 10_000e6, "shareholders_equity": 4_000e6, "current_liabilities": 2_000e6,
              "cash_and_equivalents": 500e6, **override}
    fields = {k: int(v) for k, v in fields.items() if v is not None}
    return {"period_end": END, "fields": fields, "field_sources": {k: "A-1" for k in fields}}


def test_liabilities_are_current_plus_face_lines_and_close_the_identity():
    proposal = dr.propose_liabilities(facts(NO_TOTAL))
    assert proposal["status"] == dr.DRAFT_CLEAN and proposal["value"] == 6_000_000_000
    assert proposal["identity_residual"] == 0
    assert [c["tag"].split(":")[1] for c in proposal["components"]] == [
        "LiabilitiesCurrent", "LongTermDebtNoncurrent", "DeferredIncomeTaxLiabilitiesNet", "OtherLiabilitiesNoncurrent"]


def test_a_face_subtotal_of_the_lines_above_is_not_double_counted():
    face = NO_TOTAL[:6] + [row("Deferred credits and other", fact(
        "us-gaap:DeferredCreditsAndOtherLiabilitiesNoncurrent", 1_000))] + NO_TOTAL[6:]
    proposal = dr.propose_liabilities(facts(face))
    assert proposal["value"] == 6_000_000_000 and proposal["status"] == dr.DRAFT_CLEAN
    assert proposal["notes"] == ["FACE_SUBTOTAL_EXCLUDED:us-gaap:DeferredCreditsAndOtherLiabilitiesNoncurrent"]


def test_a_missed_line_fails_the_identity_and_needs_review():
    face = [r for r in NO_TOTAL if "Deferred taxes" not in r]
    # Equity unchanged, so the face no longer closes: 10,000 - 5,600 - 4,000 = 400.
    proposal = dr.propose_liabilities(facts(face))
    assert proposal["status"] == dr.DRAFT_NEEDS_REVIEW
    assert "FACE_IDENTITY_DOES_NOT_CLOSE" in proposal["flags"]


def test_cash_line_is_chosen_by_label_and_combined_tags_are_flagged():
    plain = dr.propose_cash(facts([row("Cash", fact("us-gaap:Cash", 700))] + NO_TOTAL[1:]))
    assert plain["status"] == dr.DRAFT_CLEAN and plain["value"] == 700_000_000
    with_sti = dr.propose_cash(facts([row("Cash and cash equivalents", fact(
        "us-gaap:CashCashEquivalentsAndShortTermInvestments", 700))] + NO_TOTAL[1:]))
    assert with_sti["flags"] == ["TAG_INCLUDES_SHORT_TERM_INVESTMENTS"]
    restricted = dr.propose_cash(facts(
        [row("Cash and cash equivalents", fact("us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", 700)),
         row("Restricted cash", fact("us-gaap:RestrictedCashCurrent", 50))] + NO_TOTAL[1:]))
    assert restricted["flags"] == ["TAG_INCLUDES_RESTRICTED_CASH"]


def liabilities_entry(**override):
    found = facts(NO_TOTAL)
    proposal = dr.propose_liabilities(found)
    bound, flags = dr.balance_bindings(found, balance_period(), "A-1", "total_liabilities")
    assert flags == []
    draft = {"ticker": "ACME", "cik": CIK, "period_end": END, "accession_number": "A-1",
             "source_url": "https://www.sec.gov/x.htm", "source_sha256": "0" * 64}
    return {**dr.reviewed_field_entry(draft, proposal, "total_liabilities", bound,
                                      reviewed_on="2026-10-18", note="test"), **override}


def test_reviewed_liabilities_fill_only_a_missing_field_and_must_close():
    p = balance_period()
    assert fi.validate_reviewed_field(liabilities_entry(), p["fields"], p["field_sources"]) == 6_000_000_000
    present = balance_period(total_liabilities=6_000e6)
    assert fi.validate_reviewed_field(liabilities_entry(), present["fields"], present["field_sources"]) is None
    moved = balance_period(shareholders_equity=3_900e6)  # binding no longer matches
    assert fi.validate_reviewed_field(liabilities_entry(), moved["fields"], moved["field_sources"]) is None
    wrong = liabilities_entry(value=5_000_000_000, components={"us-gaap:LiabilitiesCurrent": 5_000_000_000})
    assert fi.validate_reviewed_field(wrong, p["fields"], p["field_sources"]) is None  # identity open
    assert fi.validate_reviewed_field(liabilities_entry(status="DRAFT_CLEAN"), p["fields"], p["field_sources"]) is None


def test_normalization_fills_reviewed_liabilities_and_integrity_then_verifies(monkeypatch):
    from stock_machine.normalization import financial_periods as fp

    def instant(val, accn="A-1"):
        return {"units": {"USD": [{"val": val, "end": END, "filed": "2026-08-01", "form": "10-Q",
                                   "fy": 2026, "fp": "Q2", "accn": accn}]}}

    def flow(val):
        return {"units": {"USD": [{"val": val, "start": "2026-04-01", "end": END, "filed": "2026-08-01",
                                   "form": "10-Q", "fy": 2026, "fp": "Q2", "accn": "A-1"}]}}

    companyfacts = {"cik": int(CIK), "facts": {"us-gaap": {
        "Revenues": flow(5_000_000_000),
        "Assets": instant(10_000_000_000), "StockholdersEquity": instant(4_000_000_000),
        "LiabilitiesCurrent": instant(2_000_000_000),
        "CashAndCashEquivalentsAtCarryingValue": instant(500_000_000),
        "DebtAndCapitalLeaseObligations": instant(3_000_000_000),
    }}}
    quarters, _, _ = fp.build_periods(companyfacts)
    assert "MISSING_BALANCE_FIELD:total_liabilities" in fi.balance_sheet_check(quarters[-1])["reasons"]
    monkeypatch.setattr(fp, "reviewed_balance_fields", lambda: (liabilities_entry(),))
    quarters, _, _ = fp.build_periods(companyfacts)
    latest = quarters[-1]
    assert latest["fields"]["total_liabilities"] == 6_000_000_000
    assert latest["fields"]["_financial_provenance"]["total_liabilities"]["tag"] == (
        "REVIEWED:CURRENT_PLUS_FACE_NONCURRENT_LIABILITY_LINES")
    assert fi.balance_sheet_check(latest)["status"] == "VERIFIED"


# ---- zero debt ----

DEBT_FREE = [r for r in BASE if "debt" not in r]


def zero_draft(face, notes=()):
    raw = filing(face, notes)
    found = dr.consolidated_facts(raw, cik=CIK, period_end=END)
    dimensional = dr.consolidated_facts(raw, cik=CIK, period_end=END, dimensional=True)
    return found, dr.propose_zero_debt(found, dimensional)


def test_zero_is_proposed_only_when_no_obligation_is_disclosed_anywhere():
    _, proposal = zero_draft(DEBT_FREE, [row("Revolver capacity", fact(
        "us-gaap:LineOfCreditFacilityMaximumBorrowingCapacity", 500, ctx="c-seg", ident="cap"))])
    assert proposal["status"] == dr.DRAFT_NEEDS_REVIEW and proposal["total_debt"] == 0
    assert proposal["method"] == fi.ZERO_DEBT_METHOD and proposal["flags"] == ["ZERO_DEBT_PROPOSED"]
    assert [c["tag"] for c in proposal["excluded_non_obligations"]] == [
        "us-gaap:LineOfCreditFacilityMaximumBorrowingCapacity"]


def test_finance_leases_drawn_lines_or_segment_debt_block_zero():
    for tag, ctx in (("us-gaap:FinanceLeaseLiability", "c-1"), ("us-gaap:LineOfCredit", "c-1"),
                     ("us-gaap:LongTermDebt", "c-seg")):
        _, proposal = zero_draft(DEBT_FREE, [row("In the notes", fact(tag, 50, ctx=ctx, ident="n"))])
        assert proposal["status"] == dr.NO_PROPOSAL and proposal["total_debt"] is None, tag
        assert "DEBT_FACTS_DISCLOSED_ELSEWHERE" in proposal["flags"]


def zero_entry():
    found, proposal = zero_draft(DEBT_FREE)
    bound, _ = dr.bindings(found, period(), "A-1")
    draft = {"ticker": "ACME", "cik": CIK, "period_end": END, "accession_number": "A-1",
             "source_url": "https://www.sec.gov/x.htm", "source_sha256": "0" * 64,
             "proposal": proposal, "binding_fields": bound}
    return dr.reviewed_entry(draft, reviewed_on="2026-10-18", note="test")


def test_reviewed_zero_debt_verifies_only_when_bound_and_uncontradicted():
    entry = zero_entry()
    p = period()
    assert fi.validate_reviewed_debt(entry, p["fields"], p["field_sources"])[0] == 0
    # A normalized debt fact contradicts the review.
    debt = period(long_term_debt=1e6)
    assert fi.validate_reviewed_debt(entry, debt["fields"], debt["field_sources"])[0] is None
    other = period(accession="A-2")
    assert fi.validate_reviewed_debt(entry, other["fields"], other["field_sources"])[0] is None
    assert fi.validate_reviewed_debt({**entry, "binding_fields": {}}, p["fields"], p["field_sources"])[0] is None
    assert fi.validate_reviewed_debt({**entry, "total_debt": 5}, p["fields"], p["field_sources"])[0] is None
    # Without the reviewed method, an empty review is still never zero.
    plain = {k: v for k, v in entry.items() if k != "method"}
    assert fi.validate_reviewed_debt(plain, p["fields"], p["field_sources"])[0] is None


def test_zero_debt_evidence_reports_its_basis():
    entry = zero_entry()
    p = {**period(), "fields": {**period()["fields"], "_reviewed_debt": entry}}
    evidence = fi.debt_evidence(p)
    assert evidence["status"] == "VERIFIED" and evidence["total_debt"] == 0
    assert evidence["basis"] == "REVIEWED_NO_DEBT_DISCLOSED"
    assert evidence["net_debt"] == -500_000_000
