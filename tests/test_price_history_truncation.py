"""A shorter-window provider must never replace a longer stored price history."""

from pathlib import Path

import pytest

from stock_machine import db
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

ROOT = Path(__file__).resolve().parents[1]


def rows(start, end, price=100.0):
    return [{"date": d, "open": price, "high": price * 1.01, "low": price * 0.99, "close": price,
             "adj_close": price, "volume": 1_000_000} for d in session_dates(start, end)]


def stored(pg, ticker):  # noqa: F811
    with pg() as conn:
        return conn.execute("SELECT min(date)::text, max(date)::text, count(*) FROM prices_daily WHERE ticker=%s",
                            (ticker,)).fetchone()


def test_a_five_year_refresh_cannot_replace_fifteen_years(pg, monkeypatch):  # noqa: F811
    monkeypatch.setattr("stock_machine.market_calendar.latest_completed_session", lambda *a, **k: "2026-10-19")
    with pg() as conn:
        db.replace_prices(conn, "SPY", rows("2011-10-20", "2026-10-19"))
    full = stored(pg, "SPY")
    with pg() as conn, pytest.raises(ValueError, match="would delete history before 2021-10-20"):
        db.replace_prices(conn, "SPY", rows("2021-10-20", "2026-10-19"))
    assert stored(pg, "SPY") == full                                   # existing history retained


def test_a_slid_or_longer_window_and_a_new_listing_are_accepted(pg, monkeypatch):  # noqa: F811
    monkeypatch.setattr("stock_machine.market_calendar.latest_completed_session", lambda *a, **k: "2026-10-19")
    with pg() as conn:
        db.replace_prices(conn, "SPY", rows("2011-10-20", "2026-08-31"))
        db.replace_prices(conn, "SPY", rows("2011-12-20", "2026-10-19"))   # a 15-year window refreshed late
        assert stored(pg, "SPY")[0] == "2011-12-20"
        db.replace_prices(conn, "SPY", rows("2006-01-03", "2026-10-19"))   # a longer history replaces freely
        assert stored(pg, "SPY")[0] == "2006-01-03"
        db.replace_prices(conn, "RIVN", rows("2021-11-10", "2026-10-19"))  # nothing stored yet
        assert stored(pg, "RIVN")[0] == "2021-11-10"


def test_the_repair_job_is_yahoo_only_main_only_and_validates_its_input():
    text = (ROOT / ".github/workflows/price-history-repair.yml").read_text()
    assert "if: github.ref == 'refs/heads/main'" in text and "PRICE_SOURCE: yahoo\n" in text
    run_lines = [line for line in text.splitlines() if "refresh_prices.py" in line]
    assert run_lines == ["        run: python scripts/refresh_prices.py $TICKERS --force"]
    assert "${{ inputs.tickers }}" not in "\n".join(run_lines) and "grep -Eq" in text
