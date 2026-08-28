import json
import logging
from typing import Any

from utils import (
    get_fetch_geosplit,
    get_fetch_prices,
    get_fetch_scalable,
    get_incognito_value_factor,
)
from position.justetf_position import JustETFPosition
from position.scalable_position import ScalablePosition
from position.yfinance_position import YFinancePosition
from scalable import _SCALABLE as SCALABLE
from logger import attach_color_stderr_handler_for_module

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF
CACHE_FILENAME = "cache.json"
# Per-ISIN value in ``cache.json`` (written by ``_save_position_in_cache``).
_CACHE_LAST_PRICE = "last_price"
_CACHE_COUNTRIES = "countries"


def _parse_cache_entry(entry: Any) -> tuple[float | None, dict[str, float] | None]:
    """
    Returns ``(last_price, cached_countries)``.
    ``cached_countries`` is ``None`` if there are no stored weights (fetch at use).
    Country values in the file are fractions of 1 (e.g. ``0.89`` for 89%).
    """
    if not isinstance(entry, dict):
        return None, None
    lp = entry.get(_CACHE_LAST_PRICE)
    if lp is None:
        lp = 0
    co = entry.get(_CACHE_COUNTRIES)
    if co is None:
        return float(lp), None
    return float(lp), {str(k): float(v) for k, v in co.items()}


def _load_cache() -> dict[str, Any]:
    logger.info("Factory: loading cache from %s", CACHE_FILENAME)
    try:
        with open(CACHE_FILENAME, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info("Factory: cache file not found, creating empty cache")
        with open(CACHE_FILENAME, "w") as f:
            json.dump({}, f, indent=2)
        return {}


def _countries_to_cache_fractions(
    rows: list[dict[str, float | str]] | None,
) -> dict[str, float]:
    if not rows:
        return {}
    return {str(r["name"]): float(r["weight_pct"]) / 100.0 for r in rows}


def _save_position_in_cache(
    cache: dict[str, Any],
    isin: str,
    *,
    last_price: float | None = None,
    countries: list[dict[str, float | str]] | None = None,
    update_price: bool = False,
    update_countries: bool = False,
) -> None:
    if not update_price and not update_countries:
        return
    row = cache.get(isin)
    if not isinstance(row, dict):
        row = {}
    else:
        row = dict(row)
    if update_price and last_price is not None:
        row[_CACHE_LAST_PRICE] = last_price
    if update_countries:
        row[_CACHE_COUNTRIES] = _countries_to_cache_fractions(countries)
    cache[isin] = row
    with open(CACHE_FILENAME, "w") as f:
        json.dump(cache, f, indent=2)


def factory(
    isin: str,
    name: str | None = None,
    short_name: str | None = None,
    shares: float | None = None,
    value: float | None = None,
    broker: str | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
    dmem_other: float | None = None,
    *,
    value_scale: float | None = None,
    price: float | None = None,
) -> JustETFPosition | YFinancePosition | ScalablePosition:
    if value_scale is None:
        logger.info("Factory: no value scale provided, using default value")
        value_scale = get_incognito_value_factor()
    cache = _load_cache()
    cached_last_price, cached_countries = _parse_cache_entry(cache.get(isin))
    fetch_prices = get_fetch_prices()
    fetch_geosplit = get_fetch_geosplit()
    use_scalable = get_fetch_scalable() and broker == SCALABLE

    if use_scalable:
        last_price = None if fetch_prices else cached_last_price
        ctor_price = price
    elif fetch_prices:
        last_price = None
        ctor_price = None
    else:
        last_price = cached_last_price
        ctor_price = None if last_price is not None else price

    scrape_geosplit = fetch_geosplit and not (
        POSITION_SOURCE == YFINANCE and not use_scalable
    )
    if scrape_geosplit:
        countries_arg: dict[str, float] | None = None
    else:
        countries_arg = cached_countries if cached_countries is not None else {}

    if use_scalable:
        position = ScalablePosition(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            dmem,
            usavn,
            dmem_other,
            last_price,
            cached_countries=countries_arg,
            value_scale=value_scale,
            price=ctor_price,
        )
    elif POSITION_SOURCE == YFINANCE:
        position = YFinancePosition(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            dmem,
            usavn,
            dmem_other,
            last_price,
            cached_countries=countries_arg,
            value_scale=value_scale,
            price=ctor_price,
        )
    elif POSITION_SOURCE == JUSTETF:
        position = JustETFPosition(
            isin,
            name,
            short_name,
            shares,
            value,
            broker,
            dmem,
            usavn,
            dmem_other,
            last_price,
            cached_countries=countries_arg,
            value_scale=value_scale,
            price=ctor_price,
        )
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")

    update_price = fetch_prices and isin is not None and position.last_price is not None
    update_countries = (
        scrape_geosplit
        and isin is not None
        and isinstance(position, JustETFPosition)
    )
    if update_price or update_countries:
        countries_rows = (
            position.countries() if update_countries else None
        )
        _save_position_in_cache(
            cache,
            isin,
            last_price=position.last_price,
            countries=countries_rows,
            update_price=update_price,
            update_countries=update_countries,
        )
    return position
