"""The SEC User-Agent comes from the SEC_USER_AGENT variable; an unset variable is never an empty header."""

import importlib
from pathlib import Path

import pytest

from stock_machine import config

ROOT = Path(__file__).resolve().parents[1]
SEC_JOBS = ("daily-data-refresh", "universe-sync", "recover-sec-fundamentals", "package-1-production-recovery",
            "strategy-forward")


@pytest.fixture
def reload(monkeypatch):
    def load(value):
        if value is None:
            monkeypatch.delenv("SEC_USER_AGENT", raising=False)
        else:
            monkeypatch.setenv("SEC_USER_AGENT", value)
        return importlib.reload(config)
    yield load
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    importlib.reload(config)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_or_empty_falls_back_to_the_default(reload, value):
    loaded = reload(value)
    assert loaded.SEC_USER_AGENT == loaded.DEFAULT_SEC_USER_AGENT and not loaded.SEC_CONTACT_CONFIGURED


def test_a_contact_user_agent_is_used(reload):
    loaded = reload(" StockMachine research ops@example.org ")
    assert loaded.SEC_USER_AGENT == "StockMachine research ops@example.org" and loaded.SEC_CONTACT_CONFIGURED


@pytest.mark.parametrize("job", SEC_JOBS)
def test_every_job_that_reads_the_sec_passes_the_variable(job):
    assert "SEC_USER_AGENT: ${{ vars.SEC_USER_AGENT }}" in (ROOT / f".github/workflows/{job}.yml").read_text()
