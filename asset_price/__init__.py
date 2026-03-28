"""ISIN-based price sources (JustETF, Yahoo Finance)."""

import json

from asset_price.justetf_position import JustETFPosition
from asset_price.yfinance_position import YFinancePosition

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF
CACHE_FILENAME = "cache.json"


def _save_cache(cache: dict[str, float]) -> None:
    with open(CACHE_FILENAME, "w") as f:
        json.dump(cache, f)


def _load_cache() -> dict[str, float]:
    try:
        with open(CACHE_FILENAME, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_price_in_cache(cache: dict[str, float], price: float, isin: str) -> None:
    cache[isin] = price
    _save_cache(cache)


def make_position(
    isin: str,
    shares: float | None = None,
    value: float | None = None,
    broker: str | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
) -> JustETFPosition | YFinancePosition:
    cache = _load_cache()
    last_price = cache.get(isin)
    if POSITION_SOURCE == YFINANCE:
        position = YFinancePosition(isin, shares, value, broker, dmem, usavn, last_price)
    elif POSITION_SOURCE == JUSTETF:
        position = JustETFPosition(isin, shares, value, broker, dmem, usavn, last_price)
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")
    if last_price is None and isin is not None:
        _save_price_in_cache(cache, position.last_price, isin)
    return position
