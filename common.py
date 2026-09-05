"""Shared constants (portfolio bucket keys, etc.)."""

EQUITY_PORTFOLIO = "equity_portfolio"
FIXED_MATURITY_BOND_PORTFOLIO = "fixed_maturity_bond_portfolio"
CASH_PORTFOLIO = "cash_portfolio"
BOND_PORTFOLIO = "bond_portfolio"
COMMODITY_PORTFOLIO = "commodity_portfolio"
PENSION_PORTFOLIO = "pension_portfolio"

ISIN_TO_PORTFOLIO: dict[str, str] = {
    "IE000BI8OT95": EQUITY_PORTFOLIO,
    "IE00BKM4GZ66": EQUITY_PORTFOLIO,
    "LU2903252349": EQUITY_PORTFOLIO,
    "IE00B4YBJ215": EQUITY_PORTFOLIO,
    "IE00BD4TXV59": EQUITY_PORTFOLIO,
    "IE00BTJRMP35": EQUITY_PORTFOLIO,
    "IE0006WW1TQ4": EQUITY_PORTFOLIO,
    "IE00BLNMYC90": EQUITY_PORTFOLIO,
    "IE00BD1F4M44": EQUITY_PORTFOLIO,
    "DE000EWG2LD7": COMMODITY_PORTFOLIO,
    "LU2300294316": FIXED_MATURITY_BOND_PORTFOLIO,
    "LU2233156582": FIXED_MATURITY_BOND_PORTFOLIO,
}
DEFAULT_ISIN_PORTFOLIO_BUCKET = EQUITY_PORTFOLIO

# Fresh estimates collected while portfolio Position objects are constructed.
# Keying by ISIN and scraped value distinguishes the same ETF held in multiple
# OSKAR entries without involving non-OSKAR positions.
PENDING_OSKAR_SHARES: dict[tuple[str, float], float] = {}

