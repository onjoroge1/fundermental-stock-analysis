from datetime import date

from stock_machine.data_quality import (
    assess_dataset, build_report, content_hash, readiness_for_snapshots,
)


def _quarter(end="2026-06-30"):
    return {
        "duration_type": "quarter", "period_end": end,
        "available_at": "2026-08-01",
        "fields": {
            "revenue": 100, "net_income": 10, "diluted_eps": 1,
            "operating_cash_flow": 12, "total_assets": 500,
            "shareholders_equity": 200,
        },
    }


def test_content_hash_is_stable_across_row_order():
    rows = [{"date": "2026-08-20", "close": 100},
            {"date": "2026-08-19", "close": 99}]
    assert content_hash(rows) == content_hash(list(reversed(rows)))


def test_quality_assessment_fails_incomplete_required_history():
    fundamentals = assess_dataset(
        "fundamentals", [_quarter()], as_of=date(2026, 8, 21),
    )
    prices = assess_dataset("prices", [], as_of=date(2026, 8, 21))
    assert fundamentals["status"] == "FAIL"
    assert prices["status"] == "FAIL"


def test_fresh_prices_and_complete_fundamentals_pass():
    quarters = [_quarter(f"2025-{month:02d}-28")
                for month in (3, 6, 9, 12)]
    prices = [{"date": "2026-08-20", "close": 100, "volume": 1000}]
    assert assess_dataset("fundamentals", quarters)["status"] == "PASS"
    assert assess_dataset(
        "prices", prices, as_of=date(2026, 8, 21),
    )["status"] == "PASS"


def test_optional_vendor_data_is_pending_not_a_blocker():
    snapshots = {
        name: {"status": "PASS", "reasons": []}
        for name in ("fundamentals", "prices", "filings")
    }
    snapshots["consensus"] = {"status": "PENDING", "reasons": ["missing"]}
    snapshots["prices"]["max_record_date"] = "2026-08-20"
    readiness = readiness_for_snapshots(snapshots, as_of=date(2026, 8, 21))
    assert readiness["status"] == "READY"
    assert readiness["trade_eligible"] is True


def test_stale_required_manifest_blocks_trade_research():
    snapshots = {
        name: {"status": "PASS", "reasons": [],
               "observed_at": "2026-08-01T12:00:00+00:00"}
        for name in ("fundamentals", "prices", "filings")
    }
    readiness = readiness_for_snapshots(snapshots, as_of=date(2026, 8, 21))
    assert readiness["status"] == "BLOCKED"
    assert any("has not refreshed" in reason
               for reason in readiness["blockers"])


def test_report_blocks_ticker_without_recorded_required_snapshots():
    report = build_report(
        [{"ticker": "AAPL", "legal_name": "Apple"}], [],
        as_of=date(2026, 8, 21),
    )
    assert report["summary"]["BLOCKED"] == 1
    assert report["tickers"][0]["readiness"]["trade_eligible"] is False
