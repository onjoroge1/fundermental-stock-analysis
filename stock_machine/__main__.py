"""CLI: python -m stock_machine <command> ...

Commands:
  all TICKER            ingest + normalize + load + write current bundle
  ingest TICKER         ingest + normalize + load only
  bundle TICKER [ISO]   write bundle as of timestamp (default: now)
  serve                 run the MCP server (stdio)
  outcomes              score due forecast outcomes + print calibration summary
  backtest [START]      walk-forward backtest over the universe (default 2014-01-01)
  mlrank                walk-forward ridge model on the latest backtest panel
  kpis                  compute the system KPI dashboard
  planprobe             test what the configured FMP key/plan can access
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    if cmd in ("all", "ingest"):
        from .pipeline import run
        summary = run(sys.argv[2])
        print(json.dumps(summary, indent=2))
        if cmd == "all":
            from . import db
            from .bundle import build_bundle, write_bundle
            from .peers import snapshot_metrics
            b = build_bundle(sys.argv[2])
            path = write_bundle(b)
            conn = db.connect()
            try:
                snapshot_metrics(conn, sys.argv[2].upper(), b)
            finally:
                conn.close()
            print(f"bundle: {path}")
            print(f"data_quality: {b['data_quality']['status']}, "
                  f"quarters={b['financial_history']['period_count']['quarters']}, "
                  f"price={b['market_snapshot']['price']}")
    elif cmd == "bundle":
        from .bundle import build_bundle, write_bundle
        as_of = sys.argv[3] if len(sys.argv) > 3 else None
        b = build_bundle(sys.argv[2], as_of)
        path = write_bundle(b)
        print(f"bundle: {path}")
        print(json.dumps(b["data_quality"], indent=2))
    elif cmd == "serve":
        from .mcp_server.server import main as serve
        serve()
    elif cmd == "outcomes":
        from . import db, outcomes
        conn = db.connect()
        try:
            db.init_schema(conn)
            result = outcomes.run(conn)
            print(json.dumps(result, indent=1, default=str))
            print(json.dumps(outcomes.summary(conn), indent=1))
        finally:
            conn.close()
    elif cmd == "backtest":
        from datetime import datetime, timezone

        from psycopg.types.json import Jsonb

        from . import db
        from .backtest import engine, evaluate
        from .config import DATA_DIR

        start = sys.argv[2] if len(sys.argv) > 2 else "2014-01-01"
        conn = db.connect()
        try:
            db.init_schema(conn)
            obs, grid = engine.run(
                conn, start=start,
                progress=lambda t, n: print(f"  {t}: {n} observations"))
            results = {h: evaluate.evaluate(obs, h)
                       for h in ("fwd_3m_pct", "fwd_6m_pct", "fwd_12m_pct")}
            run_id = ("bt_" + datetime.now(timezone.utc)
                      .strftime("%Y%m%dT%H%M%SZ"))
            summary = {"caveats": engine.CAVEATS,
                       "observations": len(obs),
                       "grid": {"start": grid[0], "end": grid[-1],
                                "dates": len(grid)},
                       "results": results}
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sm_backtest_runs (run_id, params, summary) "
                    "VALUES (%s, %s, %s)",
                    (run_id, Jsonb({"start": start}), Jsonb(summary)))
                cur.executemany(
                    """INSERT INTO backtest_observations VALUES (%(run_id)s,
                       %(as_of)s, %(ticker)s, %(sector)s, %(composite)s,
                       %(components)s, %(factors)s, %(forward)s)""",
                    [{"run_id": run_id, **o,
                      "components": Jsonb(o["components"]),
                      "factors": Jsonb(o["factors"]),
                      "forward": Jsonb(o["forward"])} for o in obs])
            conn.commit()
            out_dir = DATA_DIR / "backtests"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{run_id}.json"
            path.write_text(json.dumps(summary, indent=1))
            print(f"\nrun {run_id} → {path}")
            print(json.dumps({h: r["verdict"] for h, r in results.items()},
                             indent=1))
        finally:
            conn.close()
    elif cmd == "paper":
        from . import db, paper
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        conn = db.connect()
        try:
            if sub == "sync":
                print(json.dumps(paper.sync_with_reports(conn), indent=1))
            elif sub == "mark":
                print(json.dumps(paper.mark(conn), indent=1))
            else:
                s = paper.status(conn)
                print(json.dumps({"latest": s["latest"],
                                  "recent_closes": s["recent_closes"],
                                  "conventions": s["conventions"]}, indent=1))
        finally:
            conn.close()
    elif cmd == "mlrank":
        from datetime import datetime, timezone

        from . import db
        from .backtest.model import walk_forward
        from .baserates import load_panel
        from .config import DATA_DIR

        conn = db.connect()
        try:
            panel = load_panel(conn)
        finally:
            conn.close()
        result = walk_forward(panel)
        out_dir = DATA_DIR / "backtests"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"ml_{stamp}.json"
        path.write_text(json.dumps(result, indent=1))
        compact = {k: v for k, v in result.items() if k != "per_date"}
        print(json.dumps(compact, indent=1))
        print(f"→ {path}")
    elif cmd == "kpis":
        from . import db
        from .kpis import compute_kpis
        conn = db.connect()
        try:
            print(json.dumps(compute_kpis(conn), indent=1))
        finally:
            conn.close()
    elif cmd == "planprobe":
        from .ingestion.estimates import _get
        probes = [
            ("/stable/analyst-estimates", {"symbol": "AAPL",
             "period": "annual", "limit": 40}, "annual estimates, deep"),
            ("/stable/analyst-estimates", {"symbol": "AAPL",
             "period": "quarter", "limit": 5}, "quarterly estimates"),
            ("/stable/earnings", {"symbol": "AAPL", "limit": 40},
             "earnings history, deep"),
            ("/stable/earnings", {"symbol": "TXN", "limit": 5},
             "mid-cap symbol coverage"),
            ("/stable/earning-call-transcript", {"symbol": "AAPL",
             "year": 2026, "quarter": 2}, "transcripts"),
            ("/stable/historical-price-eod/full", {"symbol": "BBBYQ",
             "limit": 5}, "delisted prices (survivorship-free)"),
            ("/stable/delisted-companies", {"page": 0, "limit": 5},
             "delisted-companies list"),
        ]
        for path, params, label in probes:
            payload, err = _get(path, params)
            status = "OK" if not err else (
                "PLAN-GATED" if err["event"] == "PROVIDER_PLAN_LIMIT"
                else "ERROR")
            print(f"{status:10s} {label:38s} {path}")
        print("\nAfter upgrading the plan, re-run this probe — capabilities "
              "activate automatically, no code changes needed.")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
