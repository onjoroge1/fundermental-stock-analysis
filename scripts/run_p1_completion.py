"""Run every P1 challenger on one point-in-time panel and persist the verdict."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.engine import CAVEATS, run
from stock_machine.backtest.regime_panel import enrich as enrich_regime
from stock_machine.backtest.macro_panel import enrich as enrich_macro
from stock_machine.backtest.options_panel import enrich as enrich_options
from stock_machine.backtest.options_model import walk_forward as options_walk
from stock_machine.backtest.nonlinear_model import walk_forward as nonlinear_walk
from stock_machine.backtest.meta_model import walk_forward as ensemble_walk
from stock_machine.backtest.p1_store import save

MIN_OPTION_COVERAGE = 0.60
MIN_OPTION_TICKERS = 8


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2014-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None

    with db.connect() as conn:
        base, grid = run(conn, start=start, end=end)
        regime_rows, regime_cov = enrich_regime(conn, base)
        macro_rows, macro_cov = enrich_macro(conn, regime_rows)
        rows, option_cov = enrich_options(conn, macro_rows)

    options_result = options_walk(rows)
    nonlinear_result = nonlinear_walk(rows)
    ensemble_result = ensemble_walk(rows)

    data_gate = (
        option_cov.get("coverage", 0.0) >= MIN_OPTION_COVERAGE
        and option_cov.get("tickers_with_history", 0) >= MIN_OPTION_TICKERS
    )
    candidate_gates = {
        "options_ridge": bool(options_result.get("verdict", {}).get("options_model_beats_all_controls")),
        "lightgbm": bool(nonlinear_result.get("verdict", {}).get("lightgbm_beats_all_controls")),
        "ensemble": bool(ensemble_result.get("verdict", {}).get("ensemble_beats_best_single_model")),
    }

    if not data_gate:
        decision = "PENDING_MORE_OPTION_HISTORY"
        selected = None
        reason = "Historical option-surface coverage is below the P1 completion gate; no backfill is fabricated."
    elif candidate_gates["ensemble"]:
        decision = "ELIGIBLE_FOR_P1_PROMOTION_REVIEW"
        selected = "rolling_p1_ensemble"
        reason = "The causal rolling ensemble beat its constituents and option-history coverage passed."
    elif candidate_gates["lightgbm"]:
        decision = "ELIGIBLE_FOR_P1_PROMOTION_REVIEW"
        selected = "lightgbm_cross_sectional"
        reason = "The nonlinear challenger beat all controls; ensemble did not add enough incremental edge."
    elif candidate_gates["options_ridge"]:
        decision = "ELIGIBLE_FOR_P1_PROMOTION_REVIEW"
        selected = "options_implied_ridge"
        reason = "Options-implied ridge beat prior controls; more complex challengers did not improve it."
    else:
        decision = "REJECT_P1_COMPLETION_CHALLENGERS"
        selected = None
        reason = "No remaining P1 challenger beat the strongest prior control out of sample."

    result = {
        "schema_version": "p1_completion.v1",
        "panel": {
            "requested_start": start,
            "requested_end": end,
            "grid_dates": len(grid),
            "caveats": CAVEATS,
        },
        "coverage": {
            "regime": regime_cov,
            "macro": macro_cov,
            "options": option_cov,
        },
        "models": {
            "options_ridge": options_result,
            "lightgbm": nonlinear_result,
            "ensemble": ensemble_result,
        },
        "promotion": {
            "decision": decision,
            "selected_candidate": selected,
            "data_gate": data_gate,
            "candidate_gates": candidate_gates,
            "deployed_as_primary": False,
            "minimum_option_surface_coverage": MIN_OPTION_COVERAGE,
            "minimum_option_tickers": MIN_OPTION_TICKERS,
            "reason": reason,
        },
    }

    with db.connect() as conn:
        run_id = save(conn, result, rows)
    result["run_id"] = run_id
    print(json.dumps(result, indent=2, sort_keys=True))
    # PENDING/REJECT are valid research outcomes, not process failures.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
