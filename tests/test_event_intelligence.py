from stock_machine.events import screen


def _coverage(event_type: str, *, status: str = "AVAILABLE",
              observed_on: str = "2026-08-30",
              window_end: str = "2027-09-04") -> dict:
    return {
        "observed_on": observed_on,
        "source": "fmp",
        "coverage_status": status,
        "window_start": "2026-08-23",
        "window_end": window_end,
        "detail": {"method": "bounded_calendar"},
    }


def _patch_state(monkeypatch, events=None, coverage=None):
    events = events or {}
    coverage = coverage or {
        "EARNINGS": _coverage("EARNINGS"),
        "EX_DIVIDEND": _coverage("EX_DIVIDEND"),
        "SPLIT": _coverage("SPLIT"),
    }

    monkeypatch.setattr(
        screen,
        "latest_coverage",
        lambda conn, ticker, event_type, as_of=None: coverage.get(event_type),
    )

    def fake_events(conn, ticker, event_type, start_date, end_date, as_of=None):
        return [
            row for row in events.get(event_type, [])
            if start_date[:10] <= row["event_date"] <= end_date[:10]
        ]

    monkeypatch.setattr(screen, "events_in_window", fake_events)


def test_clear_when_event_coverage_is_complete_and_window_empty(monkeypatch):
    _patch_state(monkeypatch)
    result = screen.build_event_screen(
        object(), "AAPL", "call_calendar", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "CLEAR"
    assert result["reasons"] == []


def test_missing_event_coverage_fails_closed(monkeypatch):
    coverage = {
        "EARNINGS": None,
        "EX_DIVIDEND": _coverage("EX_DIVIDEND"),
        "SPLIT": _coverage("SPLIT"),
    }
    _patch_state(monkeypatch, coverage=coverage)
    result = screen.build_event_screen(
        object(), "AAPL", "put_calendar", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "BLOCK"
    assert any("earnings coverage is missing" in r for r in result["reasons"])


def test_partial_calendar_coverage_cannot_prove_no_event(monkeypatch):
    coverage = {
        "EARNINGS": _coverage("EARNINGS", status="PARTIAL"),
        "EX_DIVIDEND": _coverage("EX_DIVIDEND"),
        "SPLIT": _coverage("SPLIT"),
    }
    _patch_state(monkeypatch, coverage=coverage)
    result = screen.build_event_screen(
        object(), "AAPL", "call_diagonal", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "BLOCK"
    assert any("not AVAILABLE" in r for r in result["reasons"])


def test_earnings_before_front_expiry_blocks_all_mixed_structures(monkeypatch):
    _patch_state(monkeypatch, events={
        "EARNINGS": [{"event_date": "2026-10-01", "event_type": "EARNINGS"}]
    })
    result = screen.build_event_screen(
        object(), "AAPL", "put_diagonal", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "BLOCK"
    assert any("earnings event" in r for r in result["reasons"])


def test_ex_dividend_before_front_blocks_short_call_but_not_put(monkeypatch):
    events = {
        "EX_DIVIDEND": [
            {"event_date": "2026-09-15", "event_type": "EX_DIVIDEND"}
        ]
    }
    _patch_state(monkeypatch, events=events)
    call = screen.build_event_screen(
        object(), "AAPL", "call_calendar", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    put = screen.build_event_screen(
        object(), "AAPL", "put_calendar", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert call["status"] == "BLOCK"
    assert any("early-assignment" in r for r in call["reasons"])
    assert put["status"] == "CLEAR"
    assert any("ex-dividend" in w for w in put["warnings"])


def test_split_anywhere_through_far_expiry_blocks(monkeypatch):
    _patch_state(monkeypatch, events={
        "SPLIT": [{"event_date": "2026-12-01", "event_type": "SPLIT"}]
    })
    result = screen.build_event_screen(
        object(), "AAPL", "call_diagonal", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "BLOCK"
    assert any("stock split" in r for r in result["reasons"])


def test_stale_coverage_blocks(monkeypatch):
    stale = {
        "EARNINGS": _coverage("EARNINGS", observed_on="2026-08-20"),
        "EX_DIVIDEND": _coverage("EX_DIVIDEND", observed_on="2026-08-20"),
        "SPLIT": _coverage("SPLIT", observed_on="2026-08-20"),
    }
    _patch_state(monkeypatch, coverage=stale)
    result = screen.build_event_screen(
        object(), "AAPL", "put_calendar", "2026-10-16", "2027-01-15",
        as_of="2026-08-30",
    )
    assert result["status"] == "BLOCK"
    assert sum("stale" in r for r in result["reasons"]) == 3
