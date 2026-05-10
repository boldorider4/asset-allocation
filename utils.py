import json
from pathlib import Path


global portfolio
portfolio: dict[str, list[dict]] = {}

# Set True (e.g. via CLI) to skip reading cache.json for prices; fresh data is still written.
IGNORE_CACHE = False
FETCH_OSKAR = False
INCOGNITO = False
# Applied by ``apply_incognito_scaling``; ``Position`` / ``factory`` multiply monetary amounts by this.
INCOGNITO_VALUE_FACTOR: float = 1.0
# Optional override path for the assets JSON file; ``None`` means use the default location.
ASSETS_FILE: Path | None = None


def get_ignore_cache() -> bool:
    global IGNORE_CACHE
    return IGNORE_CACHE


def set_ignore_cache(ignore_cache: bool) -> None:
    global IGNORE_CACHE
    IGNORE_CACHE = ignore_cache


def get_fetch_oskar() -> bool:
    global FETCH_OSKAR
    return FETCH_OSKAR


def set_fetch_oskar(fetch_oskar: bool) -> None:
    global FETCH_OSKAR
    FETCH_OSKAR = fetch_oskar


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


def _incognito_cached_last_price(isin: str | None) -> float | None:
    """
    ``last_price`` from ``cache.json`` for incognito totals only.

    Uses ``position.factory``'s ``_load_cache`` / ``_parse_cache_entry`` so parsing matches
    the rest of the app. Lazy-imported to avoid cycles at ``utils`` import time.

    Returns ``None`` when there is no cache row; otherwise ``float(lp)`` (including ``0.0``
    when ``last_price`` is absent in the row).
    """
    if not isin:
        return None
    from position.factory import _load_cache, _parse_cache_entry

    lp, _ = _parse_cache_entry(_load_cache().get(isin))
    return None if lp is None else float(lp)


def apply_incognito_scaling() -> None:
    """
    Pick a random total in ``[10001, 54999]`` and set ``INCOGNITO_VALUE_FACTOR`` so that
    (when positions use cached prices / explicit JSON values) portfolio totals match that
    target. Does **not** mutate the ``portfolio`` dict; scaling is applied when building
    ``Position`` instances via ``factory`` (see ``get_incognito_value_factor``).

    Totals use explicit JSON ``value`` when set. Otherwise uses **cache.json only**
    (``shares`` × cached ``last_price``); missing cache entry or missing price → **0**
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
                lp = _incognito_cached_last_price(pos.get(ISIN))
                sh = pos.get(SHARES)
                if lp is not None and sh is not None:
                    total += float(sh) * float(lp)

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
