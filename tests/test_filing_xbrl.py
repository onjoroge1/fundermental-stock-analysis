"""A filing missing from companyfacts is normalized from its own inline XBRL."""

from stock_machine.ingestion import filing_xbrl as fx
from stock_machine.normalization.financial_periods import build_periods

CIK = "0000000042"


def html():
    ctx = lambda cid, period, seg="": (  # noqa: E731
        f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}'
        f'</xbrli:identifier>{seg}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>')
    seg = '<xbrli:segment><xbrldi:explicitMember dimension="a">b</xbrldi:explicitMember></xbrli:segment>'
    fact = lambda tag, ctxref, unit, text, scale="6", extra="": (  # noqa: E731
        f'<ix:nonFraction name="{tag}" contextRef="{ctxref}" unitRef="{unit}" decimals="-6" scale="{scale}" '
        f'format="ixt:num-dot-decimal" {extra}>{text}</ix:nonFraction>')
    return f"""<html><body><ix:header><ix:hidden>
      <ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="q">2026</ix:nonNumeric>
      <ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="q">Q2</ix:nonNumeric>
      </ix:hidden><ix:resources>
      {ctx("q", "<xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate>")}
      {ctx("i", "<xbrli:instant>2026-06-30</xbrli:instant>")}
      {ctx("s", "<xbrli:instant>2026-06-30</xbrli:instant>", seg)}
      <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure>
        </xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>
        </xbrli:unitDenominator></xbrli:divide></xbrli:unit>
      </ix:resources></ix:header>
      <table><tr><td>Revenue</td><td>{fact("us-gaap:Revenues", "q", "usd", "48,296")}</td></tr>
      <tr><td>Net loss</td><td>({fact("us-gaap:NetIncomeLoss", "q", "usd", "1,327", extra='sign="-"')})</td></tr>
      <tr><td>Diluted</td><td>{fact("us-gaap:EarningsPerShareDiluted", "q", "eps", "0.33", scale="0", extra='sign="-"')}</td></tr>
      <tr><td>Total assets</td><td>{fact("us-gaap:Assets", "i", "usd", "285,531")}</td></tr>
      <tr><td>Total assets again</td><td>{fact("us-gaap:Assets", "i", "usd", "285,531")}</td></tr>
      <tr><td>Segment assets</td><td>{fact("us-gaap:Assets", "s", "usd", "1")}</td></tr>
      </table></body></html>"""


SUBMISSIONS = {"filings": {"recent": {
    "form": ["10-Q", "8-K", "10-Q"], "accessionNumber": ["A-NEW", "A-8K", "A-OLD"],
    "filingDate": ["2026-07-29", "2026-07-20", "2026-04-30"], "reportDate": ["2026-06-30", "", "2026-03-31"],
    "primaryDocument": ["f-20260630.htm", "x.htm", "f-20260331.htm"], "isInlineXBRL": [1, 0, 1]}}}


def companyfacts():
    old = {"val": 280_000_000_000, "end": "2026-03-31", "accn": "A-OLD", "fy": 2026, "fp": "Q1",
           "form": "10-Q", "filed": "2026-04-30"}
    return {"cik": int(CIK), "facts": {"us-gaap": {"Assets": {"units": {"USD": [old]}}}}}


def test_facts_are_consolidated_signed_scaled_and_deduplicated():
    facts = fx.filing_facts(html(), cik=CIK, accession="A-NEW", filed="2026-07-29", form="10-Q")
    assert facts["Revenues"]["units"]["USD"][0] == {
        "end": "2026-06-30", "start": "2026-04-01", "val": 48_296_000_000, "accn": "A-NEW",
        "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-29"}
    assert facts["NetIncomeLoss"]["units"]["USD"][0]["val"] == -1_327_000_000
    assert facts["EarningsPerShareDiluted"]["units"]["USD/shares"][0]["val"] == -0.33
    assert [e["val"] for e in facts["Assets"]["units"]["USD"]] == [285_531_000_000]  # no repeat, no segment


def test_only_filings_newer_than_the_last_covered_one_are_lagging():
    lagging = fx.lagging_filings(SUBMISSIONS, companyfacts())
    assert [f["accession"] for f in lagging] == ["A-NEW"]


def test_supplement_merges_without_replacing_and_cites_the_filing():
    cf = companyfacts()
    saved = []
    events = fx.supplement_lagging_filings("F", CIK, SUBMISSIONS, cf, fetch=lambda url: html().encode(),
                                           save=lambda t, a, raw, url: saved.append(url))
    assert events[0]["event"] == "FILING_XBRL_SUPPLEMENTED" and events[0]["facts_added"] == 4
    assert saved == ["https://www.sec.gov/Archives/edgar/data/42/ANEW/f-20260630.htm"]
    assert [e["accn"] for e in cf["facts"]["us-gaap"]["Assets"]["units"]["USD"]] == ["A-OLD", "A-NEW"]
    # Run again: the filing is covered now, nothing is added twice.
    assert fx.supplement_lagging_filings("F", CIK, SUBMISSIONS, cf, fetch=lambda url: html().encode()) == []
    quarters, _, _ = build_periods(cf)
    latest = quarters[-1]
    assert latest["period_end"] == "2026-06-30" and latest["fields"]["revenue"] == 48_296_000_000
    assert latest["fields"]["diluted_eps"] == -0.33
    provenance = latest["fields"]["_financial_provenance"]["revenue"]
    assert provenance["source_url"].endswith("/f-20260630.htm")
    assert provenance["source_content_sha256"] == events[0]["source_content_sha256"]


def test_an_unreachable_filing_is_reported_not_raised():
    def down(url):
        raise RuntimeError("403")
    events = fx.supplement_lagging_filings("F", CIK, SUBMISSIONS, companyfacts(), fetch=down)
    assert events == [{"event": "FILING_XBRL_UNAVAILABLE", "dataset": "fundamentals", "accession": "A-NEW",
                       "source_url": "https://www.sec.gov/Archives/edgar/data/42/ANEW/f-20260630.htm",
                       "detail": "RuntimeError"}]
