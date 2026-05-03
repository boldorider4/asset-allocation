import json
from typing import Any

from asset_price import get_ignore_cache
from asset_price.justetf_position import JustETFPosition
from asset_price.yfinance_position import YFinancePosition

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF
CACHE_FILENAME = "cache.json"


def _parse_cache_entry(raw: Any) -> tuple[float | None, dict[str, float] | None]:
    """
    Returns (last_price, cached_countries).
    ``cached_countries`` is None if country weights are not in the cache (fetch at use).
    If present, values are fractions of 1 (e.g. 0.89 for 89%); see ``_save_position_in_cache``.
    Legacy entries are a single number: ``{"ISIN": 12.34}``.
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None
    if isinstance(raw, dict):
        lp = raw.get("last_price")
        if lp is None:
            return None, None
        last_price = float(lp)
        co = raw.get("countries")
        if co is None:
            return last_price, None
        if isinstance(co, dict):
            return last_price, {str(k): float(v) for k, v in co.items()}
        return last_price, None
    return None, None


def _load_cache() -> dict[str, Any]:
    try:
        with open(CACHE_FILENAME, "r") as f:
            return json.load(f)
    except FileNotFoundError:
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
    last_price: float,
    countries: list[dict[str, float | str]] | None,
) -> None:
    cache[isin] = {
        "last_price": last_price,
        "countries": _countries_to_cache_fractions(countries),
    }
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
) -> JustETFPosition | YFinancePosition:
    # Always load the on-disk map so writes merge all ISINs (including with ``--no-cache``).
    cache = _load_cache()
    if get_ignore_cache():
        last_price = None
        cached_countries: dict[str, float] | None = None
    else:
        last_price, cached_countries = _parse_cache_entry(cache.get(isin))
    if POSITION_SOURCE == YFINANCE:
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
            cached_countries=cached_countries,
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
            cached_countries=cached_countries,
        )
    else:
        raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")
    if isin is not None and position.last_price is not None:
        countries = (
            position.countries() if isinstance(position, JustETFPosition) else None
        )
        _save_position_in_cache(cache, isin, position.last_price, countries)
    return position
