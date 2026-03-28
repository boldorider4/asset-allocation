"""ISIN-based price sources (JustETF, Yahoo Finance)."""

from asset_price.justetf_position import JustETFPosition
from asset_price.yfinance_position import YFinancePosition

# "yfinance" | "justetf"
POSITION_SOURCE = "justetf"


def make_position(isin: str) -> JustETFPosition | YFinancePosition:
    if POSITION_SOURCE == "yfinance":
        return YFinancePosition(isin)
    if POSITION_SOURCE == "justetf":
        return JustETFPosition(isin)
    raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")
