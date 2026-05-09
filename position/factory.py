import json
from typing import Any

from position import get_ignore_cache, get_fetch_oskar, get_assets_file
from position.justetf_position import JustETFPosition
from position.yfinance_position import YFinancePosition
from oskar import OskarEtf, fetch_oskar_etfs
from utils import portfolio as global_portfolio
from utils import write_portfolio_to_file

OSKAR = "oskar"
# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF
CACHE_FILENAME = "cache.json"
# Per-ISIN value in ``cache.json`` (written by ``_save_position_in_cache``).
_CACHE_LAST_PRICE = "last_price"
_CACHE_COUNTRIES = "countries"

# Per-ISIN cockpit ETFs from oskar (written by ``_save_position_in_cache``).
global_oskar_etfs: dict[str, OskarEtf] = {}


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
        _CACHE_LAST_PRICE: last_price,
        _CACHE_COUNTRIES: _countries_to_cache_fractions(countries),
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

    if broker == OSKAR and get_fetch_oskar():
        global global_oskar_etfs
        # fetch lazily the cockpit ETFs from oskar
        if not global_oskar_etfs:
            global_oskar_etfs = fetch_oskar_etfs()
            # update the assets file with the new oskar etfs
            for oskar_etf in global_oskar_etfs.values():
                for positions in global_portfolio.values():
                    for position in positions:
                        if position.get("isin") == oskar_etf.isin:
                            position["value"] = oskar_etf.value_eur
                            position["shares"] = None
            write_portfolio_to_file(get_assets_file())

        if isin in global_oskar_etfs:
            oskar_etf = global_oskar_etfs.get(isin)
            if oskar_etf:
                # inherit the value from the oskar etf
                value = oskar_etf.value_eur
                shares = None

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
