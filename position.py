import time

import yfinance as yf
from yfinance.exceptions import YFRateLimitError


def _fast_info_price(ticker: yf.Ticker) -> float | None:
    fast = getattr(ticker, "fast_info", None)
    if fast is None:
        return None
    for key in ("last_price", "lastPrice"):
        try:
            lp = fast.get(key) if hasattr(fast, "get") else fast[key]
        except (KeyError, TypeError, AttributeError):
            lp = None
        if lp is not None:
            return float(lp)
    return None


def _history_last_close(ticker: yf.Ticker) -> float | None:
    for period in ("1d", "5d"):
        hist = ticker.history(period=period, interval="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    return None


def _info_price(ticker: yf.Ticker) -> float | None:
    info = ticker.info or {}
    for key in ("regularMarketPrice", "previousClose", "navPrice", "currentPrice"):
        p = info.get(key)
        if p is not None:
            return float(p)
    return None


def price_from_isin(isin: str) -> float:
    _retries = 10
    _delay_s = 1
    for attempt in range(_retries):
        try:
            ticker = yf.Ticker(isin)
        except YFRateLimitError as e:
            if attempt + 1 < _retries:
                time.sleep(_delay_s)
                continue
            raise RuntimeError(
                "Yahoo Finance rate limit after "
                f"{_retries} attempts; wait and retry or reduce how often you fetch prices."
            ) from e
        p = _fast_info_price(ticker)
        if p is not None:
            return p
        p = _history_last_close(ticker)
        if p is not None:
            return p
        p = _info_price(ticker)
        if p is not None:
            return p
    raise RuntimeError(f"No price data for ISIN {isin}")


if __name__ == "__main__":
    _sample = "IE000BI8OT95"  # Amundi Core MSCI World (from asset_allocation equity list)
    price = price_from_isin(_sample)
    print(f"ISIN {_sample} -> {price:.4f}")
