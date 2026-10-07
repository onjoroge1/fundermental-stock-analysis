"""Activate the user-authorized full-universe paper release; preserve pause."""
import json

from stock_machine import agent_trading, db
from stock_machine.admin_panel.store import controls, set_trading_mode
from stock_machine.agents.contracts import AGENT_UNIVERSE, POLICY_ID


def main():
    with db.connect() as conn:
        db.init_schema(conn)
        available = {row["ticker"] for row in db.list_companies(conn)}
        missing = sorted(set(AGENT_UNIVERSE) - available)
        if missing:
            raise RuntimeError(f"UNIVERSE_COMPANIES_MISSING: {missing}")
        capture = controls(conn)
    mode = set_trading_mode(
        "production-universe-release", "PAPER", None,
        "User requested all covered stocks monitored and paper traded; reviewed universe release",
    )
    print(json.dumps({
        "status": "PAPER_CONFIGURED", "policy_id": POLICY_ID,
        "universe_count": len(AGENT_UNIVERSE), "mode": mode["mode"],
        "capture_paused": capture["capture_paused"],
        "max_gross_pct": agent_trading.MAX_GROSS_PCT,
        "target_position_pct": agent_trading.TARGET_POSITION_PCT,
        "broker_submission": False,
    }))


if __name__ == "__main__":
    main()
