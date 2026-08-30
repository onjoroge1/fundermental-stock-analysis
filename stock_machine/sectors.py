"""Deterministic SIC-code → sector taxonomy.

Classification logic over the SEC's real SIC codes — coarse on purpose:
sector-relative statistics need enough peers per bucket to mean anything.
First matching range wins (specific before broad)."""
from __future__ import annotations

SIC_RANGES: list[tuple[int, int, str]] = [
    (3674, 3674, "Semiconductors"),
    (3559, 3559, "Semiconductors"),        # semicap equipment (AMAT, LRCX)
    (3827, 3827, "Semiconductors"),        # process metrology (KLAC)
    (3570, 3579, "Technology Hardware"),   # computers, storage, networking
    (3600, 3699, "Technology Hardware"),
    (3711, 3716, "Automobiles"),
    (7370, 7389, "Software & Internet"),
    (7800, 7899, "Software & Internet"),   # streaming/media services (NFLX)
    (8000, 8099, "Healthcare Services"),   # physician/health services (HIMS)
    (6000, 6299, "Banks & Consumer Finance"),
    (4500, 4599, "Airlines"),
    (4800, 4899, "Telecom & Cable"),
    (5000, 5999, "Consumer & Retail"),
    (4700, 4799, "Consumer & Retail"),     # travel services (BKNG)
    (7340, 7340, "Consumer & Retail"),     # lodging marketplaces (ABNB)
    (2000, 2399, "Consumer & Retail"),     # food, beverage, apparel
    (3020, 3199, "Consumer & Retail"),     # footwear & leather (NKE)
]


def classify(sic: str | int | None) -> str:
    if sic in (None, ""):
        return "Unclassified"
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unclassified"
    for lo, hi, sector in SIC_RANGES:
        if lo <= code <= hi:
            return sector
    return "Other"


# Coverage universe. Specialized sectors are allowed, but their data-quality
# and model-suitability gates determine whether a stock can be scored/traded.
UNIVERSE = [
    # software & internet
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",  # existing
    "CRM", "ADBE", "ORCL", "NOW", "INTU", "IBM", "NFLX", "UBER", "ABNB",
    "BKNG", "PLTR",
    # semiconductors
    "AVGO", "AMD", "QCOM", "TXN", "MU", "INTC", "AMAT", "LRCX", "KLAC", "ADI",
    # technology hardware
    "CSCO", "DELL", "HPQ",
    # consumer & retail
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "TGT", "LOW", "CMG",
    # healthcare services
    "HIMS",
    # automobiles
    "F", "GM", "RIVN",
    # telecom & cable
    "VZ", "T", "TMUS", "CMCSA",
    # airlines
    "DAL", "AAL", "UAL", "LUV",
    # media
    "DIS",
    # fintech/bank (out of core scope — gate decides what is scorable)
    "SOFI",
]
