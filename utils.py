import json
from pathlib import Path


global portfolio
portfolio: dict[str, list[dict]] = {}

# Set True (e.g. via ``--fetch-prices``) to skip reading cache.json for quotes; fresh price is then written back.
IGNORE_CACHE = False
FETCH_PRICES = False
FETCH_GEOSPLIT = False
FETCH_OSKAR = False
FETCH_SCALABLE = False
INCOGNITO = False
# Applied by ``apply_incognito_scaling``; ``Position`` / ``factory`` multiply monetary amounts by this.
INCOGNITO_VALUE_FACTOR: float = 1.0
# Optional override path for the assets JSON file; ``None`` means use the default location.
ASSETS_FILE: Path | None = None


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


def _incognito_cached_price(isin: str | None) -> float | None:
    """
    ``price`` from ``cache.json`` for incognito totals only.

    Uses ``position.factory``'s ``_load_cache`` / ``_parse_cache_entry`` so parsing matches
    the rest of the app. Lazy-imported to avoid cycles at ``utils`` import time.

    Returns ``None`` when there is no cache row or the row has no ``price``;
    otherwise ``float(cached)``.
    """
    if not isin:
        return None
    from position.factory import _load_cache, _parse_cache_entry

    cached, _ = _parse_cache_entry(_load_cache().get(isin))
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

    Lazy-imports factory helpers to avoid import cycles with ``utils``.
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
