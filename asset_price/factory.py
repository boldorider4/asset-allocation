import json

from asset_price import get_ignore_cache
from asset_price.justetf_position import JustETFPosition
from asset_price.yfinance_position import YFinancePosition

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF
CACHE_FILENAME = "cache.json"


def _load_cache() -> dict[str, float]:
    try:
        with open(CACHE_FILENAME, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_price_in_cache(cache: dict[str, float], price: float, isin: str) -> None:
    cache[isin] = price
    with open(CACHE_FILENAME, "w") as f:
        json.dump(cache, f)


def factory(
    isin: str,
    name: str | None = None,
    shares: float | None = None,
    value: float | None = None,
    broker: str | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
    dmem_other: float | None = None,
) -> JustETFPosition | YFinancePosition:
    if get_ignore_cache():
        cache: dict[str, float] = {}
        last_price = None
    else:
        cache = _load_cache()
        last_price = cache.get(isin)
    if POSITION_SOURCE == YFINANCE:
        position = YFinancePosition(isin, name, shares, value, broker, dmem, usavn, dmem_other, last_price)
    elif POSITION_SOURCE == JUSTETF:
        position = JustETFPosition(isin, name, shares, value, broker, dmem, usavn, dmem_other, last_price)
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")
    if not get_ignore_cache() and last_price is None and isin is not None:
        _save_price_in_cache(cache, position.last_price, isin)
    return position
