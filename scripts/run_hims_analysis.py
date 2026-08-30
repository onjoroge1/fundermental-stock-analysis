"""Build and persist the current HIMS analyst-layer report."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_analyses import build_report, save  # noqa: E402
from specs_hims import HIMS_SPEC             # noqa: E402


if __name__ == "__main__":
    save(build_report("HIMS", HIMS_SPEC))
