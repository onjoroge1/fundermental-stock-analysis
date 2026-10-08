"""Daily bounded technical evaluation after the completed-session data refresh."""

import json
from pathlib import Path
from stock_machine.agent_intelligence.technical_setup_store import run_daily


def main():
    result = run_daily()
    directory = Path("data/technical_setups")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (result["as_of"] + ".json")).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 1 if result["status"] not in ("OK", "SKIPPED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
