"""Central paths and settings. Everything lives under the project data/ tree.
Secrets come from .env (never committed)."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("STOCK_MACHINE_DATA", PROJECT_ROOT / "data"))

RAW_DIR = DATA_DIR / "raw"
BUNDLE_DIR = DATA_DIR / "bundles"
REPORT_DIR = DATA_DIR / "reports"


def _load_dotenv() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# SEC fair-access policy: identify yourself and stay under 10 req/s.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "StockMachine research kim.njo@gmail.com"
)
SEC_MIN_REQUEST_INTERVAL_S = 0.15

# Optional paid providers for point-in-time consensus estimates.
FMP_API_KEY = os.environ.get("FMP_API_KEY")
ALPHAVANTAGE_API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY")


def ensure_dirs() -> None:
    for p in (RAW_DIR, BUNDLE_DIR, REPORT_DIR):
        p.mkdir(parents=True, exist_ok=True)
