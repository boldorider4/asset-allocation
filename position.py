import yfinance as yf


def _price_from_isin(isin: str) -> float:
    ticker = yf.Ticker(isin)
    hist = ticker.history(period="5d")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    fast = getattr(ticker, "fast_info", None)
    if fast is not None:
        lp = fast.get("last_price")
        if lp is not None:
            return float(lp)
    info = ticker.info or {}
    for key in ("regularMarketPrice", "previousClose", "navPrice"):
        p = info.get(key)
        if p is not None:
            return float(p)
    raise RuntimeError(f"No price data for ISIN {isin}")


if __name__ == "__main__":
    _sample = "IE000BI8OT95"  # Amundi Core MSCI World (from asset_allocation equity list)
    price = _price_from_isin(_sample)
    print(f"ISIN {_sample} -> {price:.4f}")
