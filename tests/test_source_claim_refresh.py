"""Latest-report source claim refresh stays factual, idempotent and non-trading."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import stock_machine.source_claim_refresh as refresh_mod
from stock_machine.claim_evidence import FACT_FIELDS


def _bundle(price=100.0):
    return {
        "company": {"ticker": "AAPL"},
        "market_snapshot": {"price": price, "price_date": "2026-09-18", "source_ids": ["YAHOO:CHART:AAPL"]},
        "financial_history": {"quarterly_periods": [], "annual_periods": []},
        "derived_metrics": {},
        "consensus": {},
        "invalidation_breaches": [],
        "data_quality": {"dataset_versions": {}},
    }


def test_fact_allowlist_covers_core_income_balance_cashflow_and_shares():
    assert FACT_FIELDS["revenue"][0] == "income_statement"
    assert FACT_FIELDS["total_assets"][0] == "balance_sheet"
    assert FACT_FIELDS["operating_cash_flow"][0] == "cash_flow"
    assert FACT_FIELDS["weighted_average_diluted_shares"][0] == "shares"
    assert len(FACT_FIELDS) >= 20


def test_source_claim_refresh_reuses_current_verified_brief(monkeypatch):
    bundle = _bundle()
    from stock_machine.research_contract import bundle_identity
    previous = {
        "method": refresh_mod.METHOD,
        "analysis_id": "brief-existing",
        "research_inputs": {
            "bundle_sha256": bundle_identity(bundle),
            "price": 100.0,
            "price_date": "2026-09-18",
        },
        "claim_validation": {"status": "VERIFIED", "verified": 6, "total": 6},
    }
    monkeypatch.setattr(refresh_mod, "read_inputs", lambda ticker: (bundle, previous, {}))
    monkeypatch.setattr(refresh_mod, "evaluate", lambda *a, **k: {"research_status": "CURRENT_VERIFIED"})
    monkeypatch.setattr(refresh_mod, "build_brief", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no rebuild")))
    result = refresh_mod.refresh("AAPL", now=datetime(2026, 9, 18, 22, tzinfo=timezone.utc))
    assert result == {
        "ticker": "AAPL", "status": "CURRENT", "replayed": True,
        "report_id": "brief-existing", "verified": 6, "total": 6,
        "research_status": "CURRENT_VERIFIED",
    }


def test_source_claim_refresh_persists_changed_bundle_once(monkeypatch):
    bundle = _bundle(price=101.0)
    previous = {"method": "legacy", "analysis_id": "legacy"}
    report = {
        "analysis_id": "brief-new", "ticker": "AAPL",
        "as_of": "2026-09-18T22:00:00+00:00",
        "claim_validation": {"status": "VERIFIED", "verified": 8, "total": 8},
    }
    calls = []

    monkeypatch.setattr(refresh_mod, "read_inputs", lambda ticker: (bundle, previous, {}))
    monkeypatch.setattr(refresh_mod, "build_brief", lambda *a, **k: report)
    monkeypatch.setattr(refresh_mod, "validate_analysis_report", lambda *a, **k: calls.append("validated"))
    monkeypatch.setattr(refresh_mod, "evaluate", lambda *a, **k: {"research_status": "WITHHELD"})

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(refresh_mod.db, "connect", lambda: Conn())
    monkeypatch.setattr(refresh_mod.db, "save_report", lambda *a, **k: calls.append(("saved", a[1])))

    result = refresh_mod.refresh("AAPL", now=datetime(2026, 9, 18, 22, tzinfo=timezone.utc))
    assert calls == ["validated", ("saved", "brief-new")]
    assert result["status"] == "REFRESHED"
    assert result["verified"] == result["total"] == 8
