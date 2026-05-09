"""ISIN-based price sources (JustETF, Yahoo Finance)."""

from pathlib import Path

# Set True (e.g. via CLI) to skip reading cache.json for prices; fresh data is still written.
IGNORE_CACHE = False
FETCH_OSKAR = False
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