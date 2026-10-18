"""Parse an SEC inline XBRL document into numeric facts, contexts and units.

Standard library only. Shared by the debt/balance reconciliation drafts (which
also use table and row placement) and the filing fallback that normalizes a
periodic filing missing from SEC companyfacts.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

_DASHES = {"", "-", "—", "–"}

DEI_FIELDS = ("dei:DocumentFiscalYearFocus", "dei:DocumentFiscalPeriodFocus",
              "dei:DocumentPeriodEndDate", "dei:DocumentType")


class InlineXbrl(HTMLParser):
    """Collect numeric facts, contexts (instant or start/end, members), units
    (including per-share divide units), document dei fields and table/row
    placement."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.facts, self.contexts, self.units = [], {}, {}
        self._tables, self._table_count = [], 0
        self._row = None
        self._cell = None
        self._open_facts = []
        self._context = self._unit = None
        self._capture = None
        self._unit_part = None
        self._dei = None
        self.dei = {}

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
            self._context = {"id": a.get("id"), "instant": None, "start": None, "end": None,
                             "entity": None, "dimensional": False, "members": []}
        elif tag in ("xbrldi:explicitmember", "xbrldi:typedmember") and self._context:
            self._context["dimensional"] = True
            self._context["members"].append([a.get("dimension") or "", ""])
            if tag == "xbrldi:explicitmember":
                self._capture = (tag, [])
        elif tag in ("xbrli:instant", "xbrli:identifier", "xbrli:startdate", "xbrli:enddate") and self._context:
            self._capture = (tag, [])
        elif tag == "xbrli:unit":
            self._unit = {"id": a.get("id"), "measures": [], "numerator": [], "denominator": []}
        elif tag in ("xbrli:unitnumerator", "xbrli:unitdenominator") and self._unit:
            self._unit_part = "numerator" if tag == "xbrli:unitnumerator" else "denominator"
        elif tag == "xbrli:measure" and self._unit:
            self._capture = (tag, [])
        elif tag == "ix:nonnumeric" and a.get("name") in DEI_FIELDS:
            self._dei = (a["name"], [])

    def handle_data(self, data):
        for fact in self._open_facts:
            fact["text"].append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._capture:
            self._capture[1].append(data)
        if self._dei:
            self._dei[1].append(data)

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
            elif tag == "xbrli:startdate":
                self._context["start"] = text
            elif tag == "xbrli:enddate":
                self._context["end"] = text
            else:
                self._unit[self._unit_part or "measures"].append(text)
            self._capture = None
        elif tag in ("xbrli:unitnumerator", "xbrli:unitdenominator"):
            self._unit_part = None
        elif tag == "ix:nonnumeric" and self._dei:
            self.dei.setdefault(self._dei[0], " ".join("".join(self._dei[1]).split()))
            self._dei = None
        elif tag == "xbrli:context" and self._context:
            self.contexts[self._context["id"]] = self._context
            self._context = None
        elif tag == "xbrli:unit" and self._unit:
            self.units[self._unit["id"]] = self._unit
            self._unit = None


def unit_name(unit: dict) -> str | None:
    """companyfacts unit names: USD, shares, USD/shares (pure and others: None)."""
    local = lambda xs: [x.split(":")[-1] for x in xs]  # noqa: E731
    if unit.get("numerator"):
        return "USD/shares" if (local(unit["numerator"]), local(unit["denominator"])) == (["USD"], ["shares"]) else None
    measures = local(unit.get("measures", []))
    return {("USD",): "USD", ("shares",): "shares"}.get(tuple(measures))


def _numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\s$()%,.\-—–0-9]+", text))


def fact_value(fact: dict) -> Decimal | None:
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


