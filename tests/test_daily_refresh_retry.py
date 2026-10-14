"""The nightly refresh marks provider refusals as retryable, and nothing else."""

import importlib.util
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location(
        "daily_refresh_under_test", Path(__file__).resolve().parents[1] / "scripts" / "daily_refresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_failures_are_retryable_and_data_errors_are_not(monkeypatch):
    from stock_machine.pipeline import PriceSourcesUnavailable
    module = load()
    def provider(t):
        raise PriceSourcesUnavailable("PRICE_SOURCES_UNAVAILABLE yahoo=[YAHOO_HTTP_429] fmp=[FMP_PRICE_HTTP_402]")
    monkeypatch.setattr(module, "run_pipeline", provider)
    entry = module._refresh_ticker("ABNB", {}, 1)
    assert entry["status"] == "error" and entry["retryable"] is True and entry["attempts"] == 1
    def data(t):
        raise KeyError("revenue")
    monkeypatch.setattr(module, "run_pipeline", data)
    assert module._refresh_ticker("ABNB", {}, 2)["retryable"] is False
