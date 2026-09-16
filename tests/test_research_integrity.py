"""Adversarial checks for source identity, date binding and withholding."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stock_machine.bundle import _period_json, STATEMENT_FIELDS
from stock_machine.claim_evidence import audit_claims
from stock_machine.financial_integrity import balance_sheet_check, debt_evidence
from stock_machine.normalization.financial_periods import build_periods
from stock_machine.research_contract import evaluate, guard_coverage_row, bundle_identity
from stock_machine.research_cycle import build_brief
from stock_machine.report_schema import validate_analysis_report, AnalysisReportValidationError

NOW = datetime(2026, 9, 16, 15, tzinfo=timezone.utc)


@pytest.fixture
def source_bundle():
    raw = json.loads((Path(__file__).parent / "fixtures/vz_2026q2_source_extract.json").read_text())
    quarters, _, _ = build_periods(raw)
    q = quarters[-1]
    assert q["period_end"] == "2026-06-30"
    return {"company": {"ticker": "VZ"}, "financial_history": {"quarterly_periods": [_period_json(q, STATEMENT_FIELDS)]},
            "market_snapshot": {"price": 42, "price_date": "2026-09-15", "source_ids": ["test-price"]},
            "data_quality": {"status": "PASS", "financial_integrity": balance_sheet_check(q)}, "invalidation_breaches": []}


def test_actual_verizon_tag_does_not_lose_noncurrent_debt(source_bundle):
    debt = source_bundle["data_quality"]["financial_integrity"]["debt"]
    assert debt["total_debt"] == 165_231_000_000
    assert debt["net_debt"] == 163_479_000_000
    assert source_bundle["data_quality"]["financial_integrity"]["status"] == "VERIFIED"
    q = source_bundle["financial_history"]["quarterly_periods"][0]
    assert q["field_provenance"]["long_term_debt"]["tag"] == "LongTermDebtAndCapitalLeaseObligations"


def test_financial_provenance_keeps_original_sec_hash_after_supplement(monkeypatch):
    from stock_machine.ingestion import sec
    from stock_machine.research_contract import digest
    from types import SimpleNamespace
    raw = json.loads((Path(__file__).parent / "fixtures/vz_2026q2_source_extract.json").read_text())
    original_hash = digest(raw)
    monkeypatch.setattr(sec, "_get", lambda url: SimpleNamespace(json=lambda: copy.deepcopy(raw)))
    monkeypatch.setattr(sec, "save_raw", lambda *a: None)
    data = sec.fetch_companyfacts("VZ", "0000732712")
    data["subsequent_test_supplement"] = {"shares": 1}
    quarters, _, _ = build_periods(data)
    hashes = {p["source_content_sha256"] for p in quarters[-1]["fields"]["_financial_provenance"].values()}
    assert hashes == {original_hash}


def test_missing_debt_and_cash_are_never_imputed():
    p = {"period_end": "2026-06-30", "fields": {"short_term_debt": 21_783_000_000, "cash_and_equivalents": 1_752_000_000},
         "field_sources": {"short_term_debt": "a", "cash_and_equivalents": "a"}}
    assert debt_evidence(p)["total_debt"] is None
    p["fields"]["reported_total_debt"] = 100
    p["field_sources"]["reported_total_debt"] = "b"
    assert debt_evidence(p)["net_debt"] is None
    assert "CASH_DEBT_SOURCE_ACCESSIONS_DIFFER" in debt_evidence(p)["reasons"]


def test_fresh_brief_binds_real_source_values(source_bundle):
    brief = build_brief(source_bundle, now=NOW)
    validate_analysis_report(brief, source_bundle=source_bundle)
    assert brief["claim_validation"]["status"] == "VERIFIED"
    contract = evaluate(source_bundle, brief, None, now=NOW)
    assert contract["research_observation_eligible"] is True
    assert contract["guidance_eligible"] is False
    assert "ANALYST_SCENARIOS_UNQUALIFIED" in contract["guidance_reasons"]


@pytest.mark.parametrize("tamper", ["value", "source", "prose", "path", "label", "date"])
def test_source_id_presence_cannot_verify_a_false_claim(source_bundle, tamper):
    brief = build_brief(source_bundle, now=NOW)
    c = brief["claims"][0]
    if tamper == "value": c["evidence"]["value"] += 1
    if tamper == "source": c["source_ids"] = ["SEC:invented"]
    if tamper == "prose": c["claim"] = "The stock is cheap because the source ID exists."
    if tamper == "path": c["evidence"]["path"] = "/company/ticker"
    if tamper == "label": c["evidence"]["label"] = "Guaranteed profit"
    if tamper == "date": brief["as_of"] = "2026-01-01T00:00:00Z"
    assert audit_claims(brief, source_bundle)["status"] == "UNVERIFIED"
    with pytest.raises(AnalysisReportValidationError):
        validate_analysis_report(brief, source_bundle=source_bundle)


def test_new_spot_cannot_renew_old_returns_or_report_date(source_bundle):
    brief = build_brief(source_bundle, now=NOW)
    changed = copy.deepcopy(source_bundle)
    changed["market_snapshot"]["price"] += 1
    result = evaluate(changed, brief, None, now=NOW)
    assert not result["research_observation_eligible"]
    assert "REPORT_INPUTS_SUPERSEDED_OR_UNBOUND" in result["reasons"]
    stale = evaluate(source_bundle, brief, None, now=NOW + timedelta(hours=37))
    assert "REPORT_EXPIRED" in stale["reasons"]
    row = guard_coverage_row({"research_contract": evaluate(source_bundle, brief, None, now=NOW),
                             "report_12m": {"expected_return_pct": 90}}, now=NOW + timedelta(hours=37))
    assert row["report_12m"] is None
    assert not row["research_contract"]["research_observation_eligible"]


def test_legacy_index_cannot_reexpose_financial_metrics():
    row = guard_coverage_row({"report_12m": {"expected_return_pct": 90}, "composite_score": 95, "ev_to_revenue_ttm": 3,
                              "signals": {"cheap": True}}, now=NOW)
    assert row["composite_score"] is row["ev_to_revenue_ttm"] is row["report_12m"] is None
    assert not any(row["signals"].values())


def test_public_cycle_route_never_reaches_provider_without_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from stock_machine.webapp_automation import app
    import stock_machine.research_cycle as cycle
    monkeypatch.setenv("STOCK_MACHINE_ADMIN_TOKEN", "a" * 32)
    monkeypatch.setattr(cycle, "run", lambda *a, **k: pytest.fail("unauthorized provider call"))
    response = TestClient(app).post("/api/admin/research/VZ/run", json={"idempotency_key": "test-request"})
    assert response.status_code == 401


def test_api_ui_mcp_index_and_journal_share_one_snapshot(monkeypatch, source_bundle):
    import asyncio
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from stock_machine import api_v1, research_contract, control_plane
    from stock_machine.agents.research import build_decision
    from stock_machine.mcp_server.api_gateway import ResearchAPI
    from stock_machine.webapp_automation import app
    brief = build_brief(source_bundle, now=datetime.now(timezone.utc))
    # Even an author trying to smuggle targets into a valid source brief
    # cannot make normal read paths display them as qualified guidance.
    brief["forecasts"] = {"twelve_month": {"expected_return_pct": 999}}
    brief["investment_thesis"]["summary"] = "BUY NOW: guaranteed riches"
    reader = lambda ticker: (source_bundle, brief, None)
    monkeypatch.setattr(api_v1, "read_inputs", reader)
    monkeypatch.setattr(research_contract, "read_inputs", reader)
    client = TestClient(app)
    api = client.get("/api/v1/stocks/VZ/research").json()
    ui = client.get("/api/report/VZ").json()
    mcp = asyncio.run(ResearchAPI(app).stock_research("VZ"))["data"]
    index = control_plane.build_index_row("VZ", bundle=source_bundle, report=brief)
    journal = build_decision("VZ", api, datetime.now(timezone.utc), source_bundle["market_snapshot"]["price_date"])
    sid = api["research_contract"]["snapshot_id"]
    assert all(r["research_contract"]["snapshot_id"] == sid for r in (ui, mcp, index))
    assert journal.research_snapshot_id == sid
    assert ui["forecasts"] == api["analysis"]["forecasts"] == {}
    assert "BUY NOW" not in json.dumps(ui)
    assert api["decision_context"]["bear_strategy_guidance"]["primary"] == "NO_RECOMMENDATION"
    assert index["report_12m"] is None
