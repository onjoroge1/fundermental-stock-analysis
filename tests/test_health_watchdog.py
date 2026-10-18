"""The health watchdog's checks classify real failure patterns correctly."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location(
        "health_watchdog", Path(__file__).resolve().parents[1] / "scripts" / "health_watchdog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wd = load()


def test_prices_healthy_and_stale():
    assert wd.check_prices({"status": "HEALTHY", "current_count": 54, "latest_market_date": "2026-10-16"})["severity"] is None
    stale = wd.check_prices({"status": "STALE", "stale_count": 54, "missing_count": 0,
                             "latest_market_date": "2026-10-09", "expected_market_date": "2026-10-12"})
    assert stale["severity"] == "critical" and "54 stale" in stale["summary"]


def test_failed_first_attempt_with_successful_retry_is_healthy():
    runs = [{"conclusion": "success", "created_at": "2026-10-14T01:18Z"},
            {"conclusion": "failure", "created_at": "2026-10-14T00:24Z"}]
    assert wd.check_workflow("daily-data-refresh.yml", "critical", runs)["severity"] is None


def test_failure_streak_is_reported_with_its_severity():
    runs = [{"conclusion": None, "created_at": "now"},            # in progress: ignored
            {"conclusion": "failure", "created_at": "b", "html_url": "u"},
            {"conclusion": "failure", "created_at": "a"},
            {"conclusion": "success", "created_at": "old"}]
    result = wd.check_workflow("option-surface-capture.yml", "warning", runs)
    assert result["severity"] == "warning" and "last 2 completed run(s) failure" in result["summary"]
    assert result["url"] == "u"
    assert wd.check_workflow("x.yml", "critical", [])["severity"] == "warning"


def decision(ticker, status="RECORDED", blockers=(), price_date="2026-10-16", decided_at="2026-10-17T05:00Z"):
    return {"ticker": ticker, "status": status, "blockers": list(blockers),
            "price_date": price_date, "decided_at": decided_at}


def test_coverage_counts_latest_decision_per_stock_for_the_session():
    rows = [decision(f"T{i}") for i in range(50)]
    rows += [decision("T0", status="BLOCKED", blockers=["FINANCIAL_DEPENDENCIES_UNVERIFIED"], decided_at="2026-10-17T09:00Z")]
    rows += [decision("OLD", price_date="2026-10-15")]          # other session ignored
    rows += [decision(f"W{i}", status="BLOCKED", blockers=["DATA_QUALITY_NOT_PASS"]) for i in range(2)]
    rows += [decision("X", status="BLOCKED", blockers=["STALE_INPUTS"])]
    result = wd.check_coverage(rows, "2026-10-16")
    assert result["covered"] == 53 and result["severity"] == "warning"
    # 49 recorded + T0's newer withheld decision + 2 withheld = 52 learnable; X is not.
    assert result["learnable"] == 52
    assert wd.check_coverage(rows[:30], "2026-10-16")["severity"] == "critical"


def test_overall_status_and_markdown():
    checks = [{"check": "a", "severity": None, "summary": "fine"},
              {"check": "b", "severity": "warning", "summary": "meh", "url": "https://x"}]
    assert wd.overall(checks) == "WARNING"
    assert wd.overall(checks + [{"check": "c", "severity": "critical", "summary": "bad"}]) == "CRITICAL"
    assert wd.overall(checks[:1]) == "OK"
    text = wd.markdown(checks, "WARNING", datetime(2026, 10, 18, 13, 15, tzinfo=timezone.utc))
    first, rest = text.split("\n", 1)
    assert first.startswith("**Health check 2026-10-18 13:15 UTC: WARNING**")
    assert rest.index("meh") < rest.index("fine") and "([run](https://x))" in rest


def test_signature_ignores_timestamps_and_counts_but_not_new_problems():
    a = [{"check": "opt", "severity": "warning", "summary": "last 9 failed; latest day 1"}]
    b = [{"check": "opt", "severity": "warning", "summary": "last 10 failed; latest day 2"}]
    assert wd.signature(a) == wd.signature(b) == "opt=warning"
    worse = b + [{"check": "prices", "severity": "critical", "summary": "stale"}]
    assert wd.signature(worse) != wd.signature(b)
    assert wd.signature([{"check": "x", "severity": None, "summary": "fine"}]) == "ok"
    assert "watchdog-signature: opt=warning" in wd.markdown(a, "WARNING", datetime(2026, 1, 2, tzinfo=timezone.utc))
