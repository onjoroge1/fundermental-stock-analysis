"""FMP surprise history: vendor-labelled, available the session after announcement."""

from scripts import fetch_fmp_surprise_history as f


def test_events_are_dated_and_available_the_session_after_announcement():
    payload = [
        {"date": "2016-01-26", "epsActual": 3.28, "epsEstimated": 3.23},   # Tuesday
        {"date": "2016-04-30", "epsActual": 1.0, "epsEstimated": 1.0},      # Saturday
        {"date": "2016-07-26", "epsActual": 1.42, "epsEstimated": None},    # no estimate: skipped
        {"date": "2016-10-25", "epsActual": 1.67, "epsEstimated": 0},       # zero estimate: skipped
        {"date": "2099-01-01", "epsActual": None, "epsEstimated": 2.0},     # future: skipped
    ]
    out = f.events(payload, "2026-10-18")
    assert [e["announcement_date"] for e in out] == ["2016-01-26", "2016-04-30"]
    assert out[0]["available_session"] == "2016-01-27"
    assert out[1]["available_session"] == "2016-05-03"  # next session after Monday 2 May
    assert out[0]["surprise_pct"] == round((3.28 - 3.23) / 3.23 * 100, 4)
