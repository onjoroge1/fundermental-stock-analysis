"""Draft debt reconciliations from a filing's inline XBRL, for human review.

Most covered issuers report debt as separate balance sheet lines that no
single global tag totals, so financial_integrity withholds them. This module
reads the pinned filing itself: the debt lines on the face balance sheet are
proposed as components, a debt-note total (when the filing has one) is used
as corroboration, and anything a reviewer must judge is flagged.

A draft never affects the gate. Only entries promoted into
normalization/reviewed_balance_sheets.json (status SOURCE_RECONCILED) are
read by financial_integrity, and promotion re-validates them there.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

VERSION = "debt-reconciliation-draft.v1"
DRAFT_CLEAN = "DRAFT_CLEAN"
DRAFT_NEEDS_REVIEW = "DRAFT_NEEDS_REVIEW"
NO_PROPOSAL = "NO_PROPOSAL"

# Local tag names that denote borrowings or finance-lease obligations.
_DEBT = re.compile(
    r"Debt|Borrowing|CommercialPaper|NotesPayable|LoansPayable|NotesAndLoans|FinanceLease|"
    r"CapitalLease|ConvertibleNotes|SeniorNotes|LineOfCredit|Bonds")
# Words that make a matching name something other than an obligation balance.
_NOT_DEBT = re.compile(
    r"Operating|Investment|Securities|Receivable|Interest|Issuance|Repayment|"
    r"Proceeds|Discount|Premium|FairValue|Asset|Maturit|Covenant|Deferred|"
    r"Rate|Term$|Expense|Payments|Extinguishment|Gain|Loss|Fee")
# Totals a debt note may disclose, used only to corroborate face lines.
NOTE_TOTALS = (
    "DebtLongtermAndShorttermCombinedAmount", "DebtAndCapitalLeaseObligations",
    "LongTermDebtAndFinanceLeaseLiabilities", "DebtAndFinanceLeaseLiabilities",
    "LongTermDebt", "DebtInstrumentCarryingAmount",
)
FINANCE_LEASE_NOTE_TAGS = ("FinanceLeaseLiability",)
BANK_TAGS = ("Deposits", "FederalFundsPurchased",
             "SecuritiesSoldUnderAgreementsToRepurchase")
BINDING_TAGS = {
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "shareholders_equity": ("StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
}
_DASHES = {"", "-", "—", "–"}


def local(tag: str) -> str:
    return tag.split(":")[-1]


def is_debt_tag(tag: str) -> bool:
    name = local(tag)
    return bool(_DEBT.search(name)) and not _NOT_DEBT.search(name)


class _InlineXbrl(HTMLParser):
    """Collect numeric facts, contexts, units and table/row placement."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.facts, self.contexts, self.units = [], {}, {}
        self._tables, self._table_count = [], 0
        self._row = None
        self._cell = None
        self._open_facts = []
        self._context = self._unit = None
        self._capture = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._table_count += 1
            self._tables.append(self._table_count)
        elif tag == "tr":
            self._row = {"cells": [], "facts": []}
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "ix:nonfraction":
            fact = {"tag": a.get("name", ""), "context_id": a.get("contextref"),
                    "unit_id": a.get("unitref"), "scale": a.get("scale", "0"),
                    "decimals": a.get("decimals"), "sign": a.get("sign"),
                    "format": a.get("format") or "", "element_id": a.get("id"),
                    "table": self._tables[-1] if self._tables else None, "text": []}
            self._open_facts.append(fact)
        elif tag == "xbrli:context":
            self._context = {"id": a.get("id"), "instant": None, "entity": None, "dimensional": False,
                             "members": []}
        elif tag in ("xbrldi:explicitmember", "xbrldi:typedmember") and self._context:
            self._context["dimensional"] = True
            self._context["members"].append([a.get("dimension") or "", ""])
            if tag == "xbrldi:explicitmember":
                self._capture = (tag, [])
        elif tag in ("xbrli:instant", "xbrli:identifier") and self._context:
            self._capture = (tag, [])
        elif tag == "xbrli:unit":
            self._unit = {"id": a.get("id"), "measures": []}
        elif tag == "xbrli:measure" and self._unit:
            self._capture = (tag, [])

    def handle_data(self, data):
        for fact in self._open_facts:
            fact["text"].append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._capture:
            self._capture[1].append(data)

    def handle_endtag(self, tag):
        if tag == "table" and self._tables:
            self._tables.pop()
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            label = next((c for c in self._row["cells"] if c and not _numeric(c)), "")
            for fact in self._row["facts"]:
                fact["label"] = label
            self._row = None
        elif tag == "ix:nonfraction" and self._open_facts:
            fact = self._open_facts.pop()
            fact["text"] = "".join(fact["text"]).strip()
            fact["label"] = ""
            self.facts.append(fact)
            if self._row is not None:
                self._row["facts"].append(fact)
        elif self._capture and tag == self._capture[0]:
            text = "".join(self._capture[1]).strip()
            if tag == "xbrldi:explicitmember":
                self._context["members"][-1][1] = text
            elif tag == "xbrli:instant":
                self._context["instant"] = text
            elif tag == "xbrli:identifier":
                self._context["entity"] = text
            else:
                self._unit["measures"].append(text)
            self._capture = None
        elif tag == "xbrli:context" and self._context:
            self.contexts[self._context["id"]] = self._context
            self._context = None
        elif tag == "xbrli:unit" and self._unit:
            self.units[self._unit["id"]] = self._unit
            self._unit = None


def _numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\s$()%,.\-—–0-9]+", text))


def _value(fact: dict) -> Decimal | None:
    text = fact["text"].replace(",", "").replace(" ", "").replace("\xa0", "")
    if text in _DASHES or fact["format"].endswith(("zerodash", "fixed-zero")):
        value = Decimal(0)
    else:
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
    value *= Decimal(10) ** int(fact["scale"] or 0)
    return -value if fact["sign"] == "-" else value


def consolidated_facts(raw: str, *, cik: str, period_end: str, dimensional: bool = False) -> list[dict]:
    """Non-dimensional USD facts at the period-end instant for this entity
    (with dimensional=True, the dimensional ones instead)."""
    parser = _InlineXbrl()
    parser.feed(raw)
    out = []
    for fact in parser.facts:
        context = parser.contexts.get(fact["context_id"]) or {}
        unit = parser.units.get(fact["unit_id"]) or {}
        measures = [m.split(":")[-1] for m in unit.get("measures", [])]
        entity = context.get("entity") or ""
        if (context.get("instant") != period_end or bool(context.get("dimensional")) != dimensional
                or not entity.isdigit() or int(entity) != int(cik) or measures != ["USD"]):
            continue
        value = _value(fact)
        if value is None:
            continue
        rounding = (Decimal(0) if fact["decimals"] in (None, "INF")
                    else Decimal("0.5") * Decimal(10) ** (-int(fact["decimals"])))
        out.append({"tag": fact["tag"], "value": value, "label": fact.get("label", ""),
                    "element_id": fact["element_id"], "context_id": fact["context_id"],
                    "table": fact["table"], "decimals": fact["decimals"],
                    "rounding": rounding,
                    "members": [f"{d}={m}" for d, m in context.get("members", [])]})
    return out


def face_tables(facts: list[dict]) -> set:
    """Tables from the one with total assets through the one with total
    liabilities and equity (a balance sheet may span a page break)."""
    starts = sorted(f["table"] for f in facts if local(f["tag"]) == "Assets" and f["table"])
    if not starts:
        return set()
    start = starts[0]
    ends = sorted(f["table"] for f in facts
                  if local(f["tag"]) == "LiabilitiesAndStockholdersEquity" and f["table"]
                  and f["table"] >= start)
    end = ends[0] if ends else start
    return set(range(start, end + 1)) if end - start <= 2 else {start}


def _unique(facts):
    seen, out = set(), []
    for f in facts:
        key = (f["tag"], f["value"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def propose(facts: list[dict], dimensional: list[dict] = ()) -> dict:
    """Proposed components, corroboration and review flags (pure).

    `dimensional` are the period's dimensional facts. A face debt line reported
    only in a dimensional context (e.g. split by related party or segment)
    cannot be summed from consolidated lines, so the draft is not promotable.
    """
    tables = face_tables(facts)
    face = [f for f in facts if f["table"] in tables]
    flags = []
    if not tables:
        flags.append("FACE_BALANCE_SHEET_NOT_FOUND")
    split = _unique([f for f in dimensional if f["table"] in tables and is_debt_tag(f["tag"]) and f["value"]])
    if split:
        flags.append("FACE_DEBT_LINES_IN_DIMENSIONAL_CONTEXTS")
    lines = _unique([f for f in face if is_debt_tag(f["tag"]) and f["value"] != 0])
    if any(local(f["tag"]) in BANK_TAGS for f in face):
        flags.append("BANK_BALANCE_SHEET_FUNDING_LINES_NOT_INCLUDED")
    # A face line equal to the sum of the other debt lines is their subtotal.
    for line in lines:
        others = [f for f in lines if f is not line]
        if len(others) >= 2 and abs(sum(f["value"] for f in others) - line["value"]) <= sum(
                f["rounding"] for f in lines):
            lines = others
            flags.append("FACE_SUBTOTAL_EXCLUDED:" + line["tag"])
            break
    if not lines:
        flags.append("NO_DEBT_LINES_ON_FACE_BALANCE_SHEET")
    extensions = [f["tag"] for f in lines if not f["tag"].startswith("us-gaap:")]
    if extensions:
        flags.append("ISSUER_EXTENSION_TAGS:" + ",".join(sorted(set(extensions))))
    if any("Lease" in local(f["tag"]) for f in lines):
        flags.append("FINANCE_LEASES_INCLUDED")
    elif any(local(f["tag"]) in FINANCE_LEASE_NOTE_TAGS and f["value"] for f in facts):
        flags.append("FINANCE_LEASES_DISCLOSED_OUTSIDE_FACE_LINES")
    total = sum((f["value"] for f in lines), Decimal(0))
    tolerance = sum((f["rounding"] for f in lines), Decimal(0))
    notes = _unique([f for f in facts if f["table"] not in tables
                     and local(f["tag"]) in NOTE_TOTALS])
    corroborating = [f for f in notes if lines and abs(f["value"] - total) <= tolerance + f["rounding"]]
    leases = [f for f in facts if local(f["tag"]) in FINANCE_LEASE_NOTE_TAGS and f["value"] > 0]
    lease_parts = {local(f["tag"]): f for f in facts if local(f["tag"]) in (
        "FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent")}
    if not leases and len(lease_parts) == 2:
        a, b = lease_parts.values()
        leases = [{**a, "tag": "us-gaap:FinanceLeaseLiabilityCurrent+Noncurrent",
                   "value": a["value"] + b["value"], "rounding": a["rounding"] + b["rounding"]}]
    if lines and not corroborating and leases and "FINANCE_LEASES_INCLUDED" in flags:
        # Debt-note totals often exclude finance leases that the face lines include.
        lease = leases[0]
        corroborating = [{**f, "tag": f["tag"] + "+" + lease["tag"], "value": f["value"] + lease["value"]}
                         for f in notes
                         if abs(f["value"] + lease["value"] - total) <= tolerance + f["rounding"] + lease["rounding"]]
    if lines and not corroborating:
        flags.append("NOT_CORROBORATED_BY_A_DEBT_NOTE_TOTAL")
    status = (NO_PROPOSAL if not lines or split
              else DRAFT_CLEAN if not flags else DRAFT_NEEDS_REVIEW)
    return {
        "status": status, "total_debt": int(total) if lines and not split else None,
        "components": [_public(f) for f in lines],
        "corroborated_by": [_public(f) for f in corroborating],
        "note_totals_seen": [_public(f) for f in notes],
        "dimensional_face_debt": [_public(f) for f in split],
        "flags": flags,
    }


# Facts that mention borrowing without being an obligation: undrawn capacity.
_NOT_AN_OBLIGATION = re.compile(r"Capacity|Available|Maximum|Remaining|Unused|Covenant")
from .financial_integrity import ZERO_DEBT_METHOD  # noqa: E402


def propose_zero_debt(facts: list[dict], dimensional: list[dict] = ()) -> dict:
    """Zero debt, only when the filing discloses no obligation at all.

    No debt line on the face balance sheet is not enough: any debt-like fact at
    the period end anywhere in the filing (notes, dimensional breakdowns,
    finance leases, drawn credit lines) blocks the proposal. Undrawn facility
    capacity and debt securities held as assets are not obligations.
    """
    candidates = [f for f in list(facts) + list(dimensional)
                  if is_debt_tag(f["tag"]) and f["value"] and not _NOT_AN_OBLIGATION.search(local(f["tag"]))]
    excluded = _unique([f for f in list(facts) + list(dimensional)
                        if is_debt_tag(f["tag"]) and f["value"] and _NOT_AN_OBLIGATION.search(local(f["tag"]))])
    if candidates:
        return {"status": NO_PROPOSAL, "total_debt": None, "components": [], "corroborated_by": [],
                "note_totals_seen": [], "dimensional_face_debt": [],
                "debt_facts_outside_face": [_public(f) for f in _unique(candidates)][:20],
                "flags": ["NO_DEBT_LINES_ON_FACE_BALANCE_SHEET", "DEBT_FACTS_DISCLOSED_ELSEWHERE"]}
    return {"status": DRAFT_NEEDS_REVIEW, "total_debt": 0, "method": ZERO_DEBT_METHOD, "components": [],
            "corroborated_by": [], "note_totals_seen": [], "dimensional_face_debt": [],
            "excluded_non_obligations": [_public(f) for f in excluded],
            "flags": ["ZERO_DEBT_PROPOSED"]}


def _public(f: dict) -> dict:
    out = {"tag": f["tag"], "value": int(f["value"]), "label": f["label"],
           "element_id": f["element_id"], "context_id": f["context_id"],
           "declared_decimals": f["decimals"]}
    if f.get("members"):
        out["members"] = f["members"]
    return out


def bindings(facts: list[dict], period: dict, accession: str) -> tuple[dict, list[str]]:
    """Same-accession normalized fields that equal the filing's face values."""
    fields, sources = period.get("fields") or {}, period.get("field_sources") or {}
    tables = face_tables(facts)
    face = {local(f["tag"]): f["value"] for f in facts if f["table"] in tables}
    bound, flags = {}, []
    for field, tags in BINDING_TAGS.items():
        value = fields.get(field)
        if value is None or sources.get(field) != accession:
            flags.append("NOT_NORMALIZED_FROM_THIS_FILING:" + field)
            continue
        on_face = [face.get(t) for t in tags]
        if field == "total_liabilities" and "LiabilitiesCurrent" in face and "LiabilitiesNoncurrent" in face:
            on_face.append(face["LiabilitiesCurrent"] + face["LiabilitiesNoncurrent"])
        if Decimal(str(value)) in on_face:
            bound[field] = value
        else:
            flags.append("NORMALIZED_VALUE_DIFFERS_FROM_FACE:" + field)
    return bound, flags


def reviewed_entry(draft: dict, *, reviewed_on: str, note: str) -> dict:
    """The financial_integrity format, from an approved draft."""
    keys = {}
    for c in draft["proposal"]["components"]:
        key = re.sub(r"(?<!^)(?=[A-Z])", "_", local(c["tag"])).lower()
        keys[key if key not in keys else key + "_" + c["element_id"]] = c["value"]
    method = draft["proposal"].get("method")
    entry = {
        "ticker": draft["ticker"], "cik": draft["cik"], "period_end": draft["period_end"],
        "accession_number": draft["accession_number"], "status": "SOURCE_RECONCILED",
        "source_id": f"SEC:{draft['ticker']}:{draft['period_end']}:REVIEWED_DEBT",
        "source_url": draft["source_url"], "source_sha256": draft["source_sha256"],
        "reviewed_on": reviewed_on, "units": "USD",
        "reviewed_components": keys, "binding_fields": draft["binding_fields"],
        "total_debt": draft["proposal"]["total_debt"],
        "source_element_ids": [c["element_id"] for c in draft["proposal"]["components"]],
        "review_flags_accepted": draft["proposal"]["flags"],
        "drafted_by": VERSION, "note": note,
    }
    if method:
        entry["method"] = method
    if "cash_and_equivalents" in draft["binding_fields"]:
        entry["cash_and_equivalents"] = draft["binding_fields"]["cash_and_equivalents"]
    return entry


# ---- Missing balance fields: total liabilities and cash ----------------------
# Issuers that never tag a total-liabilities line list current liabilities and
# then each non-current line. The proposal is that sum, read in document order
# between the current subtotal and the first equity line, and it is accepted
# only when the face statement's own identity closes within declared rounding:
# total liabilities and equity - lines - equity - temporary equity == 0. The
# identity checks the line selection; it never supplies the number.
_EQUITY_START = re.compile(
    r"^(CommitmentsAndContingencies|TemporaryEquity|RedeemableNoncontrolling|Redeemable|PreferredStock|"
    r"CommonStock|AdditionalPaidInCapital|RetainedEarnings|AccumulatedOtherComprehensive|"
    r"TreasuryStock|StockholdersEquity|MinorityInterest|NoncontrollingInterest|MembersEquity|"
    r"PartnersCapital|LimitedPartners)")
_LIABILITY_LINE = re.compile(
    r"Liabilit|Debt|Lease|Obligation|Deferred|Payable|Accrued|Borrowing|Notes|Tax|Pension|"
    r"Revenue|Credits|Loans")
CASH_TAGS = ("Cash", "CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "CashCashEquivalentsAndShortTermInvestments")
_CASH_LABEL = re.compile(r"^cash( and cash equivalents| and equivalents)?$", re.I)


def _face(facts: list[dict]) -> list[dict]:
    tables = face_tables(facts)
    return [f for f in facts if f["table"] in tables]


def _first(face: list[dict], name: str) -> dict | None:
    return next((f for f in face if local(f["tag"]) == name), None)


def propose_liabilities(facts: list[dict]) -> dict:
    face = _face(facts)
    flags = []
    current = _first(face, "LiabilitiesCurrent")
    total_le = _first(face, "LiabilitiesAndStockholdersEquity")
    if current is None or total_le is None:
        return {"status": NO_PROPOSAL, "value": None, "components": [],
                "flags": ["FACE_CURRENT_LIABILITIES_OR_TOTAL_NOT_FOUND"]}
    noncurrent = _first(face, "LiabilitiesNoncurrent")
    if noncurrent is not None:
        lines = [current, noncurrent]
    else:
        lines, seen = [current], {current["tag"]}
        for f in face[face.index(current) + 1:]:
            name = local(f["tag"])
            if _EQUITY_START.match(name) or name == "LiabilitiesAndStockholdersEquity":
                break
            if f["value"] == 0 or f["tag"] in seen:
                continue
            seen.add(f["tag"])
            lines.append(f)
            if not _LIABILITY_LINE.search(name):
                flags.append("UNEXPECTED_LIABILITY_LINE:" + f["tag"])
    # A non-current line equal to the run of lines just above it is their
    # subtotal (e.g. AT&T's "deferred credits and other non-current liabilities").
    notes = []
    j = 2
    while j < len(lines):
        run = Decimal(0)
        for i in range(j - 1, 0, -1):
            run += lines[i]["value"]
            if j - i >= 2 and abs(run - lines[j]["value"]) <= sum(
                    (f["rounding"] for f in lines[i:j + 1]), Decimal(0)):
                notes.append("FACE_SUBTOTAL_EXCLUDED:" + lines[j]["tag"])
                del lines[j]
                break
        else:
            j += 1
    extensions = sorted({f["tag"] for f in lines if not f["tag"].startswith("us-gaap:")})
    if extensions:
        flags.append("ISSUER_EXTENSION_TAGS:" + ",".join(extensions))
    including = _first(face, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
    equity = [including] if including else [f for f in (
        _first(face, "StockholdersEquity"), _first(face, "MinorityInterest")) if f]
    temporary = [f for f in face if local(f["tag"]).startswith(
        ("TemporaryEquityCarryingAmount", "RedeemableNoncontrollingInterestEquity"))]
    used = lines + equity + temporary + [total_le]
    value = sum((f["value"] for f in lines), Decimal(0))
    residual = total_le["value"] - value - sum((f["value"] for f in equity + temporary), Decimal(0))
    bound = sum((f["rounding"] for f in used), Decimal(0))
    if not equity:
        flags.append("FACE_EQUITY_TOTAL_NOT_FOUND")
    elif abs(residual) > bound:
        flags.append("FACE_IDENTITY_DOES_NOT_CLOSE")
    return {"status": DRAFT_CLEAN if not flags else DRAFT_NEEDS_REVIEW, "value": int(value),
            "method": ("CURRENT_PLUS_NONCURRENT_SUBTOTALS" if noncurrent is not None
                       else "CURRENT_PLUS_FACE_NONCURRENT_LIABILITY_LINES"),
            "components": [_public(f) for f in lines], "identity_residual": int(residual),
            "rounding_bound": int(bound), "flags": flags, "notes": notes}


def propose_cash(facts: list[dict]) -> dict:
    """The face line labelled cash (and cash equivalents), whatever its tag."""
    face = _face(facts)
    candidates = _unique([f for f in face if local(f["tag"]) in CASH_TAGS
                          and _CASH_LABEL.match(f["label"].strip())])
    if len(candidates) != 1:
        return {"status": NO_PROPOSAL, "value": None, "components": [],
                "flags": ["FACE_CASH_LINE_NOT_UNIQUE:" + str(len(candidates))]}
    line, flags = candidates[0], []
    name = local(line["tag"])
    # Separately disclosed restricted cash (not the combined cash tag itself).
    restricted = [f for f in facts if "RestrictedCash" in local(f["tag"])
                  and not local(f["tag"]).startswith("Cash") and f["value"]]
    notes = []
    if name == "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents":
        if restricted:
            flags.append("TAG_INCLUDES_RESTRICTED_CASH")
        else:
            notes.append("COMBINED_TAG_NO_RESTRICTED_CASH_DISCLOSED")
    if name == "CashCashEquivalentsAndShortTermInvestments":
        flags.append("TAG_INCLUDES_SHORT_TERM_INVESTMENTS")
    return {"status": DRAFT_CLEAN if not flags else DRAFT_NEEDS_REVIEW, "value": int(line["value"]),
            "method": "FACE_CASH_LINE", "components": [_public(line)], "flags": flags, "notes": notes}


BALANCE_BINDINGS = {
    "total_liabilities": ("total_assets", "shareholders_equity", "current_liabilities"),
    "cash_and_equivalents": ("total_assets", "current_liabilities"),
}
_FACE_TAG = {"total_assets": ("Assets",), "current_liabilities": ("LiabilitiesCurrent",),
             "shareholders_equity": BINDING_TAGS["shareholders_equity"]}


def balance_bindings(facts: list[dict], period: dict, accession: str, field: str) -> tuple[dict, list[str]]:
    fields, sources = period.get("fields") or {}, period.get("field_sources") or {}
    face = {local(f["tag"]): f["value"] for f in _face(facts)}
    bound, flags = {}, []
    for name in BALANCE_BINDINGS[field]:
        value = fields.get(name)
        if value is None or sources.get(name) != accession:
            flags.append("NOT_NORMALIZED_FROM_THIS_FILING:" + name)
        elif Decimal(str(value)) in [face.get(t) for t in _FACE_TAG[name]]:
            bound[name] = value
        else:
            flags.append("NORMALIZED_VALUE_DIFFERS_FROM_FACE:" + name)
    return bound, flags


def reviewed_field_entry(draft: dict, proposal: dict, field: str, binding: dict, *,
                         reviewed_on: str, note: str) -> dict:
    return {
        "ticker": draft["ticker"], "cik": draft["cik"], "period_end": draft["period_end"],
        "accession_number": draft["accession_number"], "status": "SOURCE_RECONCILED",
        "field": field, "value": proposal["value"], "method": proposal["method"],
        "components": {c["tag"]: c["value"] for c in proposal["components"]},
        "source_element_ids": [c["element_id"] for c in proposal["components"]],
        "source_labels": [c["label"] for c in proposal["components"]],
        "rounding_bound": proposal.get("rounding_bound", 0),
        "binding_fields": binding,
        "source_id": f"SEC:{draft['ticker']}:{draft['period_end']}:REVIEWED_{field.upper()}",
        "source_url": draft["source_url"], "source_sha256": draft["source_sha256"],
        "reviewed_on": reviewed_on, "review_flags_accepted": proposal["flags"],
        "drafted_by": VERSION, "note": note,
    }
