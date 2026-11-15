"""Checks on the paper ledger that need no database."""
import inspect
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from stock_machine.office import config, paper


def test_no_real_strategy_has_a_rule_registered():
    assert paper.RULES == {}


def test_the_entry_deadline_is_the_next_weekdays_open_in_new_york():
    ny = ZoneInfo("America/New_York")
    assert paper.entry_deadline(date(2026, 11, 12)) == datetime(2026, 11, 13, 9, 30, tzinfo=ny)   # Thursday
    assert paper.entry_deadline(date(2026, 11, 13)) == datetime(2026, 11, 16, 9, 30, tzinfo=ny)   # Friday
    assert paper.entry_deadline(date(2026, 11, 14)) == datetime(2026, 11, 16, 9, 30, tzinfo=ny)   # Saturday


def test_the_paper_book_needs_both_switches(monkeypatch):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    monkeypatch.delenv("OFFICE_PAPER_ENABLED", raising=False)
    with pytest.raises(config.OfficeDisabled, match="OFFICE_PAPER_DISABLED"):
        paper.scan("d", date(2026, 11, 13))
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "true")
    monkeypatch.setenv("OFFICE_ENABLED", "false")
    with pytest.raises(config.OfficeDisabled, match="OFFICE_DISABLED"):
        paper.mark("b", date(2026, 11, 13))


def test_the_ledger_calls_no_model_and_no_outside_service():
    source = inspect.getsource(paper)
    for word in ("providers", "requests", "httpx", "urllib", "socket", "ibkr", "anthropic"):
        assert word not in source.lower().replace("no broker", ""), word
    assert "'BAR_OPEN'" in source and "'BAR_CLOSE'" in source and "broker_fill" not in source   # default false, never set
