"""Agent-facing PR35 option recommendation endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/options", tags=["options-recommendation"])


@router.get("/{ticker}/recommendation")
def option_recommendation(
    ticker: str,
    direction: str = Query("auto", pattern="^(auto|bearish|bullish)$"),
    horizon: str = Query("12m", pattern="^(3m|6m|12m)$"),
    capital: float | None = Query(default=None, gt=0),
    allow_delayed: bool = Query(default=True),
) -> dict:
    from .recommendation import recommend

    try:
        return recommend(
            ticker,
            direction=direction,
            horizon=horizon,
            capital=capital,
            allow_delayed=allow_delayed,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            503,
            f"option recommendation unavailable: {type(exc).__name__}: {exc}",
        )
