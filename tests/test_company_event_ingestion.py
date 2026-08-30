from datetime import date

from stock_machine.ingestion import company_events as ce


def test_earnings_normalization_filters_symbol_and_window():
    rows = [
        {"symbol": "AAPL", "date": "2026-10-29", "time": "amc", "epsEstimated": 2.1},
        {"symbol": "MSFT", "date": "2026-10-28", "time": "amc", "epsEstimated": 3.4},
        {"symbol": "AAPL", "date": "2028-01-01", "epsEstimated": 2.5},
    ]
    events = ce._normalize_earnings(
        rows, "AAPL", date(2026, 8, 30), date(2027, 8, 30)
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "EARNINGS"
    assert events[0]["event_date"] == "2026-10-29"
    assert events[0]["status"] == "SCHEDULED"


def test_dividend_date_is_normalized_as_ex_dividend():
    rows = [{
        "symbol": "AAPL",
        "date": "2026-11-09",
        "dividend": 0.26,
        "recordDate": "2026-11-10",
        "paymentDate": "2026-11-13",
        "declarationDate": "2026-10-30",
    }]
    events = ce._normalize_dividends(
        rows, "AAPL", date(2026, 8, 30), date(2027, 8, 30)
    )
    assert events[0]["event_type"] == "EX_DIVIDEND"
    assert events[0]["event_date"] == "2026-11-09"
    assert events[0]["metadata"]["record_date"] == "2026-11-10"


def test_symbol_fallback_is_partial_not_clear(monkeypatch):
    monkeypatch.setattr(
        ce,
        "_calendar",
        lambda path, start, end: (
            None,
            {"coverage_status": "PLAN_LIMIT", "reason": "premium"},
        ),
    )
    monkeypatch.setattr(
        ce,
        "_symbol_fallback",
        lambda path, ticker: ([
            {"symbol": ticker, "date": "2026-10-29", "epsEstimated": 2.1}
        ], None),
    )
    events, coverage = ce._load_type(
        "AAPL", "EARNINGS", "/stable/earnings-calendar", "/stable/earnings",
        ce._normalize_earnings, date(2026, 8, 30), date(2027, 8, 30),
    )
    assert events
    assert coverage["coverage_status"] == "PARTIAL"
    assert coverage["detail"]["method"] == "symbol_fallback"
