import json
import logging
from pathlib import Path
from typing import Any

from common import DEFAULT_ISIN_PORTFOLIO_BUCKET, ISIN_TO_PORTFOLIO, PENDING_OSKAR_SHARES
from logger import attach_color_stderr_handler_for_module

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


global portfolio
portfolio: dict[str, list[dict]] = {}

# Set True (e.g. via ``--fetch-prices``) to skip reading cache.json for quotes; fresh price is then written back.
IGNORE_CACHE = False
FETCH_PRICES = False
FETCH_GEOSPLIT = False
FETCH_OSKAR = False
FETCH_SCALABLE = False
FETCH_TRADEREPUBLIC = False
INCOGNITO = False
# Applied by ``apply_incognito_scaling``; ``Position`` / ``factory`` multiply monetary amounts by this.
INCOGNITO_VALUE_FACTOR: float = 1.0
# Optional override path for the assets JSON file; ``None`` means use the default location.
ASSETS_FILE: Path | None = None

CACHE_FILENAME = "cache.json"
# Per-ISIN value in ``cache.json`` (written by ``save_position_in_cache``).
_CACHE_PRICE = "price"
_CACHE_COUNTRIES = "countries"


def get_ignore_cache() -> bool:
    return get_fetch_prices()


def set_ignore_cache(ignore_cache: bool) -> None:
    set_fetch_prices(ignore_cache)


def get_fetch_prices() -> bool:
    global FETCH_PRICES
    return FETCH_PRICES


def set_fetch_prices(fetch_prices: bool) -> None:
    global FETCH_PRICES, IGNORE_CACHE
    FETCH_PRICES = fetch_prices
    IGNORE_CACHE = fetch_prices


def get_fetch_geosplit() -> bool:
    global FETCH_GEOSPLIT
    return FETCH_GEOSPLIT


def set_fetch_geosplit(fetch_geosplit: bool) -> None:
    global FETCH_GEOSPLIT
    FETCH_GEOSPLIT = fetch_geosplit


def get_fetch_oskar() -> bool:
    global FETCH_OSKAR
    return FETCH_OSKAR


def set_fetch_oskar(fetch_oskar: bool) -> None:
    global FETCH_OSKAR
    FETCH_OSKAR = fetch_oskar


def get_fetch_scalable() -> bool:
    global FETCH_SCALABLE
    return FETCH_SCALABLE


def set_fetch_scalable(fetch_scalable: bool) -> None:
    global FETCH_SCALABLE
    FETCH_SCALABLE = fetch_scalable


def get_fetch_traderepublic() -> bool:
    global FETCH_TRADEREPUBLIC
    return FETCH_TRADEREPUBLIC


def set_fetch_traderepublic(fetch_traderepublic: bool) -> None:
    global FETCH_TRADEREPUBLIC
    FETCH_TRADEREPUBLIC = fetch_traderepublic


def get_assets_file() -> Path | None:
    global ASSETS_FILE
    return ASSETS_FILE


def set_assets_file(assets_file: Path) -> None:
    global ASSETS_FILE
    ASSETS_FILE = assets_file


def get_incognito() -> bool:
    global INCOGNITO
    return INCOGNITO


def set_incognito(incognito: bool) -> None:
    global INCOGNITO
    INCOGNITO = incognito


def get_incognito_value_factor() -> float:
    global INCOGNITO_VALUE_FACTOR
    return INCOGNITO_VALUE_FACTOR


def set_incognito_value_factor(factor: float) -> None:
    global INCOGNITO_VALUE_FACTOR
    INCOGNITO_VALUE_FACTOR = factor


def parse_cache_entry(entry: Any) -> tuple[float | None, dict[str, float] | None]:
    """
    Returns ``(price, cached_countries)``.
    Each element is ``None`` if the row has no stored value for it (fetch at use);
    a row written by ``--fetch-geosplit`` alone has ``countries`` but no ``price``.
    Country values in the file are fractions of 1 (e.g. ``0.89`` for 89%).
    """
    if not isinstance(entry, dict):
        return None, None
    raw_price = entry.get(_CACHE_PRICE)
    price = None if raw_price is None else float(raw_price)
    co = entry.get(_CACHE_COUNTRIES)
    if co is None:
        return price, None
    return price, {str(k): float(v) for k, v in co.items()}


def load_cache() -> dict[str, Any]:
    logger.info("loading cache from %s", CACHE_FILENAME)
    try:
        with open(CACHE_FILENAME, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info("cache file not found, creating empty cache")
        with open(CACHE_FILENAME, "w") as f:
            json.dump({}, f, indent=2)
        return {}


def countries_to_cache_fractions(
    rows: list[dict[str, float | str]] | None,
) -> dict[str, float]:
    if not rows:
        return {}
    return {str(r["name"]): float(r["weight_pct"]) / 100.0 for r in rows}


def save_position_in_cache(
    cache: dict[str, Any],
    isin: str,
    *,
    price: float | None = None,
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
    if update_price and price is not None:
        row[_CACHE_PRICE] = price
    if update_countries:
        row[_CACHE_COUNTRIES] = countries_to_cache_fractions(countries)
    cache[isin] = row
    with open(CACHE_FILENAME, "w") as f:
        json.dump(cache, f, indent=2)


def cache_broker_quotes(quotes: dict[str, float | None]) -> None:
    """Write Scalable / Trade Republic unit prices to ``cache.json``.

    No-op unless ``--fetch-prices`` is set. JustETF / Yahoo quotes are never
    stored here; those are only used live (e.g. OSKAR share estimates).
    """
    if not get_fetch_prices():
        return
    to_write = {
        str(isin): float(price)
        for isin, price in quotes.items()
        if isin and price is not None
    }
    if not to_write:
        return
    cache = load_cache()
    for isin, price in to_write.items():
        row = cache.get(isin)
        row = dict(row) if isinstance(row, dict) else {}
        row[_CACHE_PRICE] = price
        cache[isin] = row
    with open(CACHE_FILENAME, "w") as f:
        json.dump(cache, f, indent=2)
    logger.info("wrote %d broker quote(s) to cache", len(to_write))


def _incognito_cached_price(isin: str | None) -> float | None:
    """
    ``price`` from ``cache.json`` for incognito totals only.

    Returns ``None`` when there is no cache row or the row has no ``price``;
    otherwise ``float(cached)``.
    """
    if not isin:
        return None
    cached, _ = parse_cache_entry(load_cache().get(isin))
    return None if cached is None else float(cached)


def apply_incognito_scaling() -> None:
    """
    Pick a random total in ``[10001, 54999]`` and set ``INCOGNITO_VALUE_FACTOR`` so that
    (when positions use cached prices / explicit JSON values) portfolio totals match that
    target. Does **not** mutate the ``portfolio`` dict; scaling is applied when building
    ``Position`` instances via ``factory`` (see ``get_incognito_value_factor``).

    Totals use explicit JSON ``value`` when set. Otherwise uses **cache.json only**
    (``shares`` × cached ``price``); missing cache entry or missing price → **0**
    for that line (no network / no ``factory``).
    """
    global portfolio

    import random

    from portfolio.portfolio import ISIN, SHARES, VALUE

    total = 0.0
    for positions in portfolio.values():
        for pos in positions:
            raw = pos.get(VALUE)
            # Explicit JSON ``value`` is authoritative; do not mix in shares × cache here.
            if raw is not None:
                total += float(raw)
            else:
                cached_price = _incognito_cached_price(pos.get(ISIN))
                sh = pos.get(SHARES)
                if cached_price is not None and sh is not None:
                    total += float(sh) * float(cached_price)

    if total <= 0:
        return

    target = float(random.randint(10001, 54999))
    factor = target / total
    set_incognito_value_factor(factor)


def _default_assets_path() -> Path:
    return Path(__file__).resolve().parent / "assets.json"


def load_portfolio(path: Path | None = None) -> dict[str, list[dict]]:
    """Load portfolio buckets from a JSON file (default: assets.json next to this module)."""
    assets_path = path or _default_assets_path()
    with assets_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("assets root must be a JSON object")
    for key, positions in data.items():
        if not isinstance(positions, list):
            raise ValueError(f"{key!r} must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise ValueError(f"{key}[{i}] must be a JSON object")
    return data


def write_portfolio_to_file(path: Path | None = None) -> None:
    """Overwrite the assets JSON file (default: assets.json next to this module) with the current global ``portfolio``."""
    assets_path = path or _default_assets_path()
    with assets_path.open("w", encoding="utf-8") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)
        f.write("\n")


def persist_oskar_shares_in_portfolio() -> None:
    """Apply all fresh OSKAR share estimates and write ``assets.json`` once."""
    if not PENDING_OSKAR_SHARES:
        return
    # Lazy import: ``scrape.oskar`` imports ``utils.portfolio``.
    from scrape.oskar import _OSKAR as OSKAR

    updated_count = 0
    try:
        for positions in portfolio.values():
            for position in positions:
                pos_broker = position.get("broker") or position.get("Broker")
                pos_isin = position.get("ISIN") or position.get("isin")
                pos_value = position.get("value")
                if pos_broker != OSKAR or not pos_isin or pos_value is None:
                    continue
                shares = PENDING_OSKAR_SHARES.get(
                    (str(pos_isin), float(pos_value))
                )
                if shares is not None:
                    position["shares"] = shares
                    updated_count += 1
        if updated_count:
            write_portfolio_to_file(get_assets_file())
            logger.info(
                "wrote %d OSKAR share estimate(s) to portfolio file",
                updated_count,
            )
    finally:
        PENDING_OSKAR_SHARES.clear()


def bucket_for_isin(isin: str) -> str:
    """Map an ISIN to a portfolio bucket; unknown ISINs fall back to equity."""
    bucket = ISIN_TO_PORTFOLIO.get(isin)
    if bucket is None:
        logger.warning(
            "unknown ISIN %s; using %r",
            isin,
            DEFAULT_ISIN_PORTFOLIO_BUCKET,
        )
        return DEFAULT_ISIN_PORTFOLIO_BUCKET
    return bucket

