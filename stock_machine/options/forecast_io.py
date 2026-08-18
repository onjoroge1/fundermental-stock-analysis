"""Load validated canonical forecasts for option ranking."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import DATA_DIR
from ..forecasts.models import ForecastDistribution


def load_latest_forecast(
    symbol: str, directory: Path | None = None
) -> ForecastDistribution | None:
    """Load the newest valid cached forecast for a symbol, if one exists."""
    symbol = symbol.upper()
    root = directory or DATA_DIR / "predictions"
    for path in sorted(root.glob(f"{symbol}_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            canonical = payload.get("forecast_distribution", payload)
            forecast = ForecastDistribution.model_validate(canonical)
        except (OSError, ValueError, TypeError):
            continue
        if forecast.symbol == symbol:
            return forecast
    return None
