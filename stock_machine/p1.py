"""Read-only P1 decision-intelligence payload for the Prediction Lab.

The P1 page must remain usable even when newly introduced optional research
tables have not yet been migrated or populated in production. Core company,
price and forecast reads remain required; macro/options/research/calibration
reads degrade to explicit PENDING states instead of turning the whole endpoint
into HTTP 500.
"""
from __future__ import annotations

from math import erf, log, sqrt
from typing import Callable, TypeVar

from . import db
from .macro import SERIES, features_as_of as macro_features_as_of, load_series
from .regime import RegimeFeatureProvider, sector_etf
from .options.surface_store import history as option_history
from .backtest.p1_store import latest as latest_p1_run
from .alpha_calibration import summary as calibration_summary

T = TypeVar("T")


def _price_rows(rows: list[dict]) -> list[dict]:
    return [{"date": r["date"], "close": r.get("close"),
             "adj_close": r.get("adj_close") or r.get("close")}
            for r in rows if r.get("adj_close") is not None or r.get("close") is not None]


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _alpha_horizon(alpha: dict, days: int) -> dict | None:
    row = (alpha.get("horizons") or {}).get(str(days))
    if not row or row.get("status") != "OK":
        return None
    expected = row.get("expected_excess_return_pct")
    sigma_pct = row.get("residual_sigma_pct")
    p_bad10 = None
    if expected is not None and sigma_pct not in (None, 0):
        mu = log(max(1e-8, 1.0 + expected / 100.0))
        sigma = sigma_pct / 100.0
        p_bad10 = _normal_cdf((log(0.90) - mu) / sigma)
    return {
        "days": days,
        "expected_excess_return_pct": expected,
        "prob_outperform": row.get("prob_outperform"),
        "prob_underperform_benchmark_by_10pct": (round(p_bad10, 3) if p_bad10 is not None else None),
        "residual_sigma_pct": sigma_pct,
        "validation": row.get("validation") or {},
    }


def _optional_read(conn, label: str, fn: Callable[[], T], fallback: T,
                   warnings: list[dict]) -> T:
    """Run an optional DB read and recover the connection after failure.

    PostgreSQL marks a transaction failed after errors such as UndefinedTable.
    Rolling back here is required before any later optional query can proceed.
    Only optional P1 enrichment reads use this helper; required core reads are
    deliberately allowed to raise.
    """
    try:
        return fn()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        warnings.append({
            "component": label,
            "status": "PENDING",
            "reason": f"{type(exc).__name__}: {exc}",
        })
        return fallback


def decision_summary(ticker: str) -> dict:
    ticker = ticker.upper()
    warnings: list[dict] = []
    with db.connect() as conn:
        # Required, long-lived production data. If these fail the endpoint
        # should fail because there is no meaningful P1 payload to return.
        company = db.fetch_company(conn, ticker) or {}
        stock = _price_rows(db.fetch_prices(conn, ticker))
        spy = _price_rows(db.fetch_prices(conn, "SPY"))
        qqq = _price_rows(db.fetch_prices(conn, "QQQ"))
        sector_symbol = sector_etf(company.get("sector"))
        sector = _price_rows(db.fetch_prices(conn, sector_symbol)) if sector_symbol else []
        stored = db.latest_prediction_forecast(conn, ticker)

        # Optional P1 tables may lag a code deployment. Do not take down the
        # Prediction Lab just because one research dataset is not migrated yet.
        macro_series = _optional_read(
            conn,
            "macro_series",
            lambda: {sid: load_series(conn, sid) for sid in SERIES},
            {sid: [] for sid in SERIES},
            warnings,
        )
        option_rows = _optional_read(
            conn,
            "option_surface_snapshots",
            lambda: option_history(conn, ticker, limit=1),
            [],
            warnings,
        )
        research = _optional_read(
            conn,
            "p1_research_runs",
            lambda: latest_p1_run(conn),
            None,
            warnings,
        )
        calibration = _optional_read(
            conn,
            "alpha_probability_outcomes",
            lambda: calibration_summary(conn),
            {
                "status": "PENDING",
                "n": 0,
                "by_horizon": {},
                "by_regime": {},
                "note": "Calibration storage is not available or populated yet.",
            },
            warnings,
        )

    if not stock:
        return {"status": "PENDING", "ticker": ticker,
                "reason": "no stored price history", "warnings": warnings}

    as_of = stock[-1]["date"]
    regime = RegimeFeatureProvider(spy_rows=spy, qqq_rows=qqq,
                                   sector_rows=sector).features_as_of(as_of)
    macro = macro_features_as_of(macro_series, as_of)
    latest_option = option_rows[0] if option_rows else None

    alpha = (stored or {}).get("alpha_forecast") or {}
    horizons = [x for x in (_alpha_horizon(alpha, d) for d in (20, 63, 126, 252)) if x]
    promotion = alpha.get("promotion") or {}
    validated = sum(bool((h.get("validation") or {}).get("passes")) for h in horizons)
    if horizons and validated == len(horizons) and promotion.get("passed_all_horizons"):
        confidence = "HIGH"
    elif validated:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    p1_result = research.get("result") if research else None
    return {
        "status": "OK",
        "ticker": ticker,
        "as_of": as_of,
        "sector": company.get("sector"),
        "sector_proxy": sector_symbol,
        "confidence": confidence,
        "warnings": warnings,
        "alpha": {
            "status": alpha.get("status", "PENDING"),
            "model": alpha.get("model"),
            "benchmark": alpha.get("benchmark"),
            "horizons": horizons,
            "current_expectation_features": alpha.get("current_expectation_features") or {},
            "promotion": promotion,
        },
        "regime": regime,
        "macro": macro,
        "options_implied": {
            "available": latest_option is not None,
            "status": "OK" if latest_option is not None else "PENDING",
            "as_of": latest_option.get("as_of") if latest_option else None,
            "features": (latest_option.get("features") if latest_option else {}),
        },
        "calibration": calibration,
        "p1_research": ({
            "run_id": research["run_id"],
            "created_at": research["created_at"],
            "promotion": (p1_result or {}).get("promotion") or {},
            "models": (p1_result or {}).get("models") or {},
            "coverage": (p1_result or {}).get("coverage") or {},
        } if research else {
            "promotion": {"decision": "PENDING_NO_P1_COMPLETION_RUN",
                          "deployed_as_primary": False}
        }),
        "methodology": [
            "Expected returns are benchmark-relative, not absolute price targets.",
            "Every displayed research model remains non-primary until its out-of-sample kill criterion passes.",
            "Option features are observed surfaces only; missing history is not backfilled.",
            "Probability calibration is scored only after each forecast horizon matures and is never used to rewrite frozen historical forecasts.",
            "Optional P1 research storage degrades to PENDING rather than failing the entire endpoint while migrations/data population catch up.",
            "The 10% downside statistic is probability of underperforming the benchmark by 10 percentage points under the residual normal approximation, not probability of a 10% stock drawdown.",
        ],
    }
