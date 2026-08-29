"""Score matured direct-alpha probabilities and print reliability diagnostics."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.alpha_calibration import score_pending, summary


def main() -> int:
    with db.connect() as conn:
        scored = score_pending(conn)
        calibration = summary(conn)
    print(json.dumps({"scoring": scored, "calibration": calibration}, indent=2,
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
