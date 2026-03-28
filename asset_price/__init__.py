"""ISIN-based price sources (JustETF, Yahoo Finance)."""

from asset_price.justetf_position import JustETFPosition
from asset_price.yfinance_position import YFinancePosition

# "yfinance" | "justetf"
YFINANCE = "yfinance"
JUSTETF = "justetf"
POSITION_SOURCE = JUSTETF


def make_position(
    isin: str,
    shares: float | None = None,
    value: float | None = None,
    broker: str | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
) -> JustETFPosition | YFinancePosition:
    if POSITION_SOURCE == YFINANCE:
        return YFinancePosition(isin, shares, value, broker, dmem, usavn)
    if POSITION_SOURCE == JUSTETF:
        return JustETFPosition(isin, shares, value, broker, dmem, usavn)
    raise ValueError(f"Unknown POSITION_SOURCE: {POSITION_SOURCE!r}")
