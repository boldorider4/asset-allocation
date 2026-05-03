"""ISIN-based price sources (JustETF, Yahoo Finance)."""

# Set True (e.g. via CLI) to skip reading cache.json for prices; fresh data is still written.
IGNORE_CACHE = False

def get_ignore_cache() -> bool:
    global IGNORE_CACHE
    return IGNORE_CACHE

def set_ignore_cache(ignore_cache: bool) -> None:
    global IGNORE_CACHE
    IGNORE_CACHE = ignore_cache