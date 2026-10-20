"""Reviewed figures a newer filing superseded are named, never used: diagnostic reasons and a read-only scan."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_machine import bundle, db
from stock_machine import financial_integrity as fi
from stock_machine import review_coverage as rc
from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import strategy_forward as sf
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

ROOT = Path(__file__).resolve().parents[1]
CIK = "0000000042"
JUNE, SEPT = "0000000042-26-000001", "0000000042-26-000002"
NOW = datetime(2026, 10, 22, 13, 15, tzinfo=timezone.utc)


def debt_review(period_end="2026-06-30", accession=JUNE, reviewed_on="2026-09-18", cik=CIK, **extra):
    return {"ticker": "TST", "cik": cik, "period_end": period_end, "accession_number": accession,
            "status": "SOURCE_RECONCILED", "source_id": f"SEC:TST:{period_end}:REVIEWED_DEBT",
            "source_url": "https://example.invalid/tst.htm", "source_sha256": "0" * 64, "reviewed_on": reviewed_on,
            "components": {"long_term_debt": 100.0}, "total_debt": 100.0, "cash_and_equivalents": 50.0, **extra}


def liabilities_review(period_end="2026-06-30", accession=JUNE, cik=CIK):
    return {"ticker": "TST", "cik": cik, "period_end": period_end, "accession_number": accession,
            "status": "SOURCE_RECONCILED", "field": "total_liabilities", "value": 600.0,
            "method": "CURRENT_PLUS_FACE_NONCURRENT_LIABILITY_LINES", "components": {"a": 200.0, "b": 400.0},
            "binding_fields": {"total_assets": 1000.0, "shareholders_equity": 400.0, "current_liabilities": 200.0},
            "rounding_bound": 0, "source_id": f"SEC:TST:{period_end}:REVIEWED_TOTAL_LIABILITIES",
            "source_url": "https://example.invalid/tst.htm", "source_sha256": "0" * 64, "reviewed_on": "2026-10-18"}


def period(period_end="2026-06-30", accession=JUNE, *, liabilities=True, debt=100.0, form="10-Q", **fields):
    values = {"long_term_debt": debt, "cash_and_equivalents": 50.0, "total_assets": 1000.0,
              "shareholders_equity": 400.0, "current_liabilities": 200.0, **fields}
    if liabilities:
        values["total_liabilities"] = 600.0
    values = {k: v for k, v in values.items() if v is not None}
    return {"period_end": period_end, "accession_number": accession, "form": form, "filed_at": "2026-10-21",
            "available_at": "2026-10-21", "derived": False, "fields": values,
            "field_sources": {k: accession for k in values}}


@pytest.fixture
def reviews(monkeypatch):
    def use(debt=(), fields=()):
        monkeypatch.setattr(fi, "reviewed_debt_sources", lambda: tuple(debt))
        monkeypatch.setattr(fi, "reviewed_balance_fields", lambda: tuple(fields))
    return use


def findings(p, cik=CIK, as_of=None):
    return rc.coverage(cik, p, fi.balance_sheet_check(p)["reasons"], as_of=as_of)


def test_a_review_for_an_earlier_period_is_named_superseded_next_to_the_generic_reason(reviews):
    reviews(debt=[debt_review()])
    assert fi.balance_sheet_check(period())["status"] == "VERIFIED"           # the reviewed quarter verifies
    new = period("2026-09-30", SEPT, debt=110.0)
    before = fi.balance_sheet_check(new)
    after = rc.annotate(copy.deepcopy(before), CIK, new)
    assert before["status"] == after["status"] == "WITHHELD"
    assert after["reasons"] == before["reasons"] + ["REVIEW_PINNED_TO_SUPERSEDED_FILING:total_debt"]
    assert "TOTAL_DEBT_NOT_SOURCE_RECONCILED" in after["reasons"]              # the drafter keys on it
    assert {k: v for k, v in after["debt"].items()} == before["debt"]          # no number moves
    [found] = after["review_coverage"]
    assert found["reviewed_period_end"] == "2026-06-30" and found["reviewed_accession"] == JUNE
    assert found["period_end"] == "2026-09-30" and found["period_accession"] == SEPT


def test_nothing_is_flagged_when_the_new_period_verifies_or_the_period_predates_every_review(reviews):
    reviews(debt=[debt_review()], fields=[liabilities_review()])
    disclosed = period("2026-09-30", SEPT, reported_total_debt=110.0)
    assert fi.balance_sheet_check(disclosed)["status"] == "VERIFIED" and findings(disclosed) == []
    older = period("2026-03-31", "0000000042-26-000000", debt=90.0, liabilities=False)
    assert "TOTAL_DEBT_NOT_SOURCE_RECONCILED" in fi.balance_sheet_check(older)["reasons"]
    assert findings(older) == []                                               # reviewed only for later periods
    new = period("2026-09-30", SEPT, debt=110.0)
    assert findings(new, as_of="2026-09-01T00:00:00+00:00") == []              # neither review existed yet
    assert [f["kind"] for f in findings(new, as_of="2026-09-30")] == ["total_debt"]   # debt reviewed 09-18 only


def test_both_kinds_are_named_and_the_newest_of_several_reviews_is_cited(reviews):
    reviews(debt=[debt_review("2026-03-31", "0000000042-26-000000", reviewed_on="2026-06-01"), debt_review()],
            fields=[liabilities_review()])
    new = period("2026-09-30", SEPT, debt=110.0, liabilities=False)
    found = findings(new)
    assert [(f["kind"], f["state"]) for f in found] == [
        ("total_debt", rc.SUPERSEDED), ("total_liabilities", rc.SUPERSEDED)]
    assert {f["reviewed_period_end"] for f in found} == {"2026-06-30"}
    assert findings(period("2026-03-31", "0000000042-26-000000", debt=100.0)) == []


def test_a_same_period_review_is_awaiting_normalization_when_it_validates_and_not_binding_when_not(reviews):
    reviews(debt=[debt_review()], fields=[liabilities_review()])
    pending = period(liabilities=False)                                       # promoted, not yet normalized
    assert [(f["kind"], f["state"]) for f in findings(pending)] == [("total_liabilities", rc.AWAITING)]
    # the debt review binds through the period's own accession; filed under another one it waits too
    elsewhere = period()
    elsewhere["accession_number"] = "0000000042-26-000009"
    assert [(f["kind"], f["state"]) for f in findings(elsewhere)] == [("total_debt", rc.AWAITING)]
    broken = period(total_assets=1001.0, liabilities=False)                   # a bound total changed
    assert {(f["kind"], f["state"]) for f in findings(broken)} == {("total_liabilities", rc.NOT_BINDING)}
    amended = period(debt=101.0)                                              # 10-Q/A restating a bound line
    assert [(f["kind"], f["state"]) for f in findings(amended)] == [("total_debt", rc.NOT_BINDING)]


def test_a_debt_review_bound_to_a_pending_cash_review_waits_for_it(reviews):
    cash = {**liabilities_review(), "field": "cash_and_equivalents", "value": 50.0, "components": {"cash": 50.0},
            "binding_fields": {"total_assets": 1000.0, "current_liabilities": 200.0}}
    reviews(debt=[debt_review()], fields=[cash])
    pending = period(cash_and_equivalents=None)
    pending["accession_number"] = "0000000042-26-000009"
    assert {(f["kind"], f["state"]) for f in findings(pending)} == {
        ("cash_and_equivalents", rc.AWAITING), ("total_debt", rc.AWAITING)}


def test_a_review_that_can_never_bind_is_not_binding_not_ahead_or_awaiting(reviews):
    # the review cites this period's own filing under a period_end one day off (a 52/53-week date)
    reviews(debt=[debt_review(period_end="2026-07-01")])
    assert [(f["kind"], f["state"]) for f in findings(period())] == [("total_debt", rc.NOT_BINDING)]
    # a same-period review that validates but carries an unpadded cik: normalization will never apply it
    reviews(fields=[{**liabilities_review(), "cik": "42"}])
    assert [(f["kind"], f["state"]) for f in findings(period(liabilities=False))] == [
        ("total_liabilities", rc.NOT_BINDING)]


def _row(debt=(), fields=(), latest=None):
    return rc.classify(CIK, "TST", latest or period("2026-09-30", SEPT, debt=None, liabilities=False,
                                                    cash_and_equivalents=None), None,
                       today=datetime(2026, 10, 22).date())


def test_the_next_step_follows_the_kinds_due_and_the_review_method(reviews):
    reviews(fields=[liabilities_review()])                                     # liabilities only (CMCSA, HPQ, T)
    row = _row(latest=period("2026-09-30", SEPT, liabilities=False, long_term_debt=None, reported_total_debt=100.0))
    assert row["state"] == "DUE" and row["next_step"] == "draft, then promote --kind balance"
    reviews(debt=[debt_review()], fields=[liabilities_review()])               # both (AMD, AAL)
    assert _row()["next_step"] == "draft, then promote --kind balance, then promote"
    selected = [{"element_id": "f-1", "tag": "us-gaap:DebtCurrent", "label": "Automotive", "members": ["a=b"],
                 "value": 1}]
    reviews(debt=[debt_review(method=rc.OWNER_SELECTED, selected_lines=selected)])   # F, GM, TMUS
    owner = _row()
    assert owner["next_step"].startswith("draft, then custom") and owner["promote"]["custom"]
    assert owner["previous_selection"] == [{"tag": "us-gaap:DebtCurrent", "members": ["a=b"], "label": "Automotive"}]
    import shlex
    words = shlex.split(rc.custom_command("docs/evidence/x"))                # pastes into a shell as printed
    assert words[2] == "custom" and words[words.index("--note") + 1].startswith("<the owner decision")
    reviews(debt=[debt_review(method=fi.ZERO_DEBT_METHOD)])                     # CMG, PLTR
    assert "promote --accept-flags if the draft is ZERO_DEBT_PROPOSED" in _row()["next_step"]
    reviews(debt=[debt_review()])
    broken = _row(latest=period(debt=101.0))
    assert broken["state"] == "NOT_BINDING" and broken["next_step"].startswith(
        f"repair: remove the total_debt entry for 2026-06-30 ({JUNE}) in reviewed_balance_sheets.json")
    assert broken["repair_promote"]["debt"] and not broken["promote"]["debt"]
    # filed under another date (52/53 weeks): the repair names the entry that actually exists
    reviews(debt=[debt_review(period_end="2026-07-01")])
    dated = _row(latest=period())
    assert dated["state"] == "NOT_BINDING" and "total_debt entry for 2026-07-01" in dated["next_step"]
    assert "reviewed_balance_sheets.json" in dated["next_step"] and "fields.json" not in dated["next_step"]
    # one kind superseded, another broken: the repair comes first and is not hidden by DUE
    cash = {**liabilities_review("2026-09-30", SEPT), "field": "cash_and_equivalents", "value": 50.0,
            "components": {"cash": 50.0}, "binding_fields": {"total_assets": 1001.0, "current_liabilities": 200.0}}
    reviews(debt=[debt_review()], fields=[cash])
    mixed = _row(latest=period("2026-09-30", SEPT, debt=110.0, cash_and_equivalents=None))
    assert mixed["state"] == "DUE" and mixed["next_step"].startswith("repair: remove the cash_and_equivalents entry")
    assert mixed["next_step"].endswith("; draft, then promote")


def test_every_real_review_is_named_superseded_by_a_later_unreviewed_quarter():
    issuers = {}
    for row in fi.reviewed_debt_sources():
        issuers.setdefault(row["cik"], set()).add("total_debt")
    for row in fi.reviewed_balance_fields():
        issuers.setdefault(row["cik"], set()).add(row["field"])
    assert len(issuers) >= 52
    for cik, kinds in issuers.items():
        latest = max(r["period_end"] for r in (*fi.reviewed_debt_sources(), *fi.reviewed_balance_fields())
                     if r["cik"] == cik)
        later = {"period_end": f"{int(latest[:4]) + 1}{latest[4:]}", "accession_number": "x", "fields": {},
                 "field_sources": {}}
        found = findings(later, cik=cik)
        assert {f["kind"] for f in found} == kinds and {f["state"] for f in found} == {rc.SUPERSEDED}, cik


def test_the_bundle_names_it_in_the_shared_reasons_list_and_still_withholds_a_newer_filing(reviews):
    reviews(debt=[debt_review()])
    new = period("2026-09-30", SEPT, debt=110.0)
    integrity = bundle._financial_integrity(new, None, CIK, "2026-10-22T09:00:00-04:00")
    assert integrity["status"] == "WITHHELD" and integrity["debt"]["total_debt"] is None
    assert integrity["reasons"][-1] == "REVIEW_PINNED_TO_SUPERSEDED_FILING:total_debt"
    assert integrity["review_coverage"][0]["kind"] == "total_debt"
    filing = {"accession_number": "y", "form": "10-K", "filed_at": "2026-10-21", "report_date": "2026-12-31"}
    current = bundle._financial_integrity(period(), filing, CIK, "2027-02-01T09:00:00-05:00")
    assert current["reasons"] == ["NEWER_FINANCIAL_FILING_NOT_NORMALIZED"] and current["review_coverage"] == []
    # the scores' withheld_reasons is the same list object: an in-place append keeps them equal
    shared = fi.balance_sheet_check(new)
    alias = shared["reasons"]
    assert rc.annotate(shared, CIK, new)["reasons"] is alias


def test_no_deciding_module_reads_the_scan_and_no_forward_tracker_fingerprints_the_new_code():
    deciding = ["agent_intelligence/orchestrator.py", "agent_intelligence/bandit.py", "agent_intelligence/state.py",
                "agent_intelligence/learning.py", "agent_intelligence/strategy_router.py",
                "agent_intelligence/direction_policy.py", "agent_intelligence/eligibility.py",
                "agent_intelligence/instructions.py", "agent_trading.py", "paper.py", "agent_cycle.py",
                "financial_integrity.py", "research_contract.py", "agents/research.py"]
    for path in deciding:
        assert not re.search(r"review_coverage", (ROOT / "stock_machine" / path).read_text()), path
    for sources in (fvf.DECISION_SOURCES, sf.DECISION_SOURCES):
        assert "review_coverage.py" not in sources and "bundle.py" not in sources


# ---- scan (Postgres) -----------------------------------------------------------

def _insert(conn, ticker, cik, periods=(), filings=()):
    conn.execute("INSERT INTO companies(ticker,cik,legal_name) VALUES (%s,%s,%s)", (ticker, cik, ticker))
    for p in periods:
        conn.execute("""INSERT INTO financial_periods(ticker,duration_type,period_end,filed_at,available_at,form,
                        accession_number,derived,fields,field_sources) VALUES (%s,'quarter',%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (ticker, p["period_end"], p["filed_at"], p["available_at"], p["form"], p["accession_number"],
                      p["derived"], json.dumps(p["fields"]), json.dumps(p["field_sources"])))
    for accession, form, filed, report in filings:
        conn.execute("INSERT INTO filings(ticker,accession_number,form,filed_at,report_date) VALUES (%s,%s,%s,%s,%s)",
                     (ticker, accession, form, filed, report))


@pytest.fixture
def scanned(pg, reviews):  # noqa: F811
    reviews(debt=[debt_review(), debt_review(cik="0000000043", accession="0000000043-26-000001"),
                  debt_review(cik="0000000046", accession="0000000046-26-000001"),
                  debt_review(cik="0000732712", accession="0000732712-26-000001"),
                  debt_review(cik="0000000047", accession="0000000047-26-000001")],
            fields=[liabilities_review(cik="0000000044", accession="0000000044-26-000001"),
                    {**liabilities_review("2026-09-30", "0000000047-26-000002", cik="0000000047"),
                     "field": "cash_and_equivalents", "value": 50.0, "components": {"cash": 50.0},
                     "binding_fields": {"total_assets": 1001.0, "current_liabilities": 200.0}}])
    with pg() as conn:
        june = period()
        _insert(conn, "DUE", CIK, [{**june, "filed_at": "2026-08-01", "available_at": "2026-08-01"},
                                   period("2026-09-30", SEPT, debt=110.0)],
                [(JUNE, "10-Q", "2026-08-01", "2026-06-30"), (SEPT, "10-Q", "2026-10-21", "2026-09-30"),
                 ("0000000042-26-000003", "10-Q/A", "2026-10-21", "2026-09-30"), ("z", "10-Q", "2026-10-21", None)])
        cur = period(accession="0000000043-26-000001")
        _insert(conn, "CUR", "0000000043", [cur], [("0000000043-26-000002", "10-Q", "2026-10-20", "2026-09-30")])
        _insert(conn, "AWT", "0000000044", [period(accession="0000000044-26-000001", liabilities=False,
                                                   long_term_debt=None, reported_total_debt=100.0)])
        _insert(conn, "TAG", "0000000046", [period(accession="0000000046-26-000001"),
                                            period("2026-09-30", "0000000046-26-000002", reported_total_debt=110.0)])
        _insert(conn, "MIX", "0000000047", [period(accession="0000000047-26-000001"),
                                            period("2026-09-30", "0000000047-26-000002", debt=110.0,
                                                   cash_and_equivalents=None)])
        conn.execute("""INSERT INTO dataset_snapshots(snapshot_id,ticker,dataset,content_hash,row_count,status,
                        last_checked_at) VALUES ('s1','DUE','fundamentals','h',2,'WARN','2026-10-21T22:00:00Z')""")
        conn.commit()
    with pg() as conn:
        yield rc.scan(conn, now=NOW)


def test_the_scan_lists_each_reviewed_issuer_with_its_state_and_what_to_run(scanned):
    rows = {r["ticker"]: r for r in scanned["rows"]}
    assert rows["DUE"]["state"] == "DUE" and rows["CUR"]["state"] == "CURRENT"
    assert rows["AWT"]["state"] == "AWAITING_NORMALIZATION" and rows["TAG"]["state"] == "NO_LONGER_NEEDED"
    assert rows["VZ"]["state"] == "NO_QUARTER"                                 # the fixture's company, no periods
    assert scanned["due"][0] == {"ticker": "DUE", "period_end": "2026-09-30", "form": "10-Q", "filed_at": "2026-10-21",
                                 "kinds": ["total_debt"], "reviewed_period_end": "2026-06-30"}
    assert [d["ticker"] for d in scanned["due"]] == ["DUE", "MIX"] and rows["MIX"]["state"] == "DUE"
    assert [b["ticker"] for b in scanned["not_binding"]] == ["MIX"]           # a broken kind is never hidden by DUE
    assert scanned["not_binding"][0]["kinds"] == ["cash_and_equivalents"]
    assert rows["DUE"]["latest_filing"]["accession_number"] == SEPT          # originals only, NULL dates ignored
    assert rows["DUE"]["latest_period"]["balance_accession"] == SEPT
    assert rows["DUE"]["last_ingested_at"].startswith("2026-10-21")
    assert scanned["filed_not_normalized"] == [{"ticker": "CUR", "form": "10-Q", "report_date": "2026-09-30",
                                                "filed_at": "2026-10-20", "days_since_filed": 2}]
    assert scanned["awaiting_normalization"] == ["AWT"]
    tool, stem = "python scripts/draft_debt_reconciliations.py", "docs/evidence/debt-reconciliation-drafts-2026-10-22"
    assert scanned["draft_command"] == f"{tool} draft --tickers DUE,MIX --out {stem}"
    assert scanned["promote_commands"] == [f"{tool} promote --drafts {stem}.json --tickers DUE,MIX"]
    assert scanned["repair_promote_commands"] == [f"{tool} promote --kind balance --drafts {stem}.json --tickers MIX"]
    import shlex
    for command in [scanned["draft_command"], *scanned["promote_commands"], *scanned["repair_promote_commands"]]:
        assert shlex.split(command)[:2] == ["python", "scripts/draft_debt_reconciliations.py"]
    assert scanned["sec_contact"] == {"nightly": None} and "Actions variable" in scanned["requires"]
    assert scanned["custom_command"] is None and scanned["custom"] == []
    assert scanned["counts"]["DUE"] == 2
    not_in_db = [r for r in scanned["rows"] if r["state"] == "NOT_IN_DATABASE"]
    assert not not_in_db                                                       # every reviewed cik is stored here
    assert set(scanned["review_files"]) == {"reviewed_balance_sheets.json", "reviewed_balance_fields.json"}


def test_the_scan_runs_in_a_read_only_transaction_and_ends_it(pg, reviews):  # noqa: F811
    reviews(debt=[debt_review()])
    seen = []
    with pg() as conn:
        conn.execute("""INSERT INTO operator_audit(actor,event,details) VALUES
            ('test','STRATEGY_FORWARD_STAGE_FINISHED','{"sec_contact_configured": true}')""")
        conn.commit()
    with pg() as conn:
        class Recording:
            def execute(self, sql, params=None):
                seen.append(sql.strip().split()[0].upper())
                return conn.execute(sql, params)

            def rollback(self):
                conn.rollback()
        out = rc.scan(Recording(), now=NOW)
        assert conn.info.transaction_status.name == "IDLE"                    # rolled back, nothing held
    assert seen == ["SET", "WITH", "SELECT"] and out["issuers"] == 1
    assert out["sec_contact"] == {"nightly": True} and out["requires"] is None     # nothing due, contact recorded
    for sql in (rc.SCAN_SQL, rc.SEC_CONTACT_SQL):
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|CREATE)\b", sql, re.I)


def test_the_route_and_the_admin_card_serve_the_scan_and_the_card_never_raises(scanned, pg, monkeypatch):  # noqa: F811
    from stock_machine.admin_panel import operations, store
    from stock_machine.webapp_automation import app
    scan = rc.scan
    monkeypatch.setattr(rc, "scan", lambda conn, now=None: scan(conn, now=NOW))   # the fixture's clock
    with TestClient(app) as client:
        body = client.get("/api/v1/financial-reviews").json()
        meta = client.get("/api/v1/meta").json()
    assert body["schema_version"] == rc.VERSION and body["due"][0]["ticker"] == "DUE"
    assert meta["routes"]["financial_review_coverage"] == "/api/v1/financial-reviews"
    monkeypatch.setattr(rc, "scan", scan)
    monkeypatch.setattr(store, "connect", pg)
    card = operations.review_coverage_summary(now=NOW)
    assert card["status"] == "OK" and card["due"] == scanned["due"] and "rows" not in card

    def down():
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "connect", down)
    assert operations.review_coverage_summary() == {"status": "UNAVAILABLE", "reason": "RuntimeError",
                                                    "broker_submission": False}


def test_the_admin_page_renders_the_review_card_with_text_only():
    html = (ROOT / "webui/admin.html").read_text()
    script = (ROOT / "webui/admin.js").read_text()
    assert 'id="reviews-summary"' in html and 'id="reviews-due"' in html and 'id="reviews-next"' in html
    assert "renderReviews(data.financial_reviews" in script and "innerHTML" not in script
