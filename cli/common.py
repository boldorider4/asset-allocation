# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared constants (portfolio bucket keys, position field keys, etc.)."""

from typing import Literal

EQUITY_PORTFOLIO = "equity_portfolio"
FIXED_MATURITY_BOND_PORTFOLIO = "fixed_maturity_bond_portfolio"
CASH_PORTFOLIO = "cash_portfolio"
BOND_PORTFOLIO = "bond_portfolio"
COMMODITY_PORTFOLIO = "commodity_portfolio"
PENSION_PORTFOLIO = "pension_portfolio"
CRYPTO_PORTFOLIO = "crypto_portfolio"
REAL_ESTATE_PORTFOLIO = "real_estate_portfolio"
PRIVATE_EQUITY_PORTFOLIO = "private_equity_portfolio"

#: Single source of truth for the canonical asset buckets: the ordered
#: ``(bucket key, display label)`` pairs (e.g. the constituents page
#: section order, where empty sections are always rendered for a fresh
#: user). Non-canonical buckets a user happens to hold are appended
#: after these.
AssetBucket = Literal[
    "equity_portfolio",
    "bond_portfolio",
    "commodity_portfolio",
    "fixed_maturity_bond_portfolio",
    "cash_portfolio",
    "pension_portfolio",
    "crypto_portfolio",
    "real_estate_portfolio",
    "private_equity_portfolio",
]
ASSET_BUCKETS: tuple[tuple[str, str], ...] = (
    (EQUITY_PORTFOLIO, "Equity"),
    (BOND_PORTFOLIO, "Fixed Income"),
    (COMMODITY_PORTFOLIO, "Commodities / Inflation"),
    (FIXED_MATURITY_BOND_PORTFOLIO, "Fixed Maturity"),
    (CASH_PORTFOLIO, "Cash"),
    (PENSION_PORTFOLIO, "Pensions"),
    (CRYPTO_PORTFOLIO, "Crypto"),
    (REAL_ESTATE_PORTFOLIO, "Real Estate"),
    (PRIVATE_EQUITY_PORTFOLIO, "Private Equity"),
)
ASSET_BUCKET_KEYS: frozenset[str] = frozenset(
    key for key, _ in ASSET_BUCKETS
)

# Portfolio user used when nothing else is configured: positions and
# portfolios are keyed by user.
DEFAULT_USER_NAME = "default"

# Canonical asset-position field keys: single source of truth for row
# dicts in portfolio storage, scrape appends, portfolios, and the web backend.
NAME = "name"
SHORT_NAME = "short_name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"
PRICE = "price"
# Developed markets vs. emerging markets breakdown
# 1 => 100% developed markets
# 0 => 100% emerging markets
DMEM = "dmem"
# Developed markets vs. other markets breakdown when country listed is "other"
# 1 => 100% of "other" is considered developed markets
# 0.5 => 50% of "other" is considered developed markets
DMEM_OTHER = "dmem_other"
# US vs. non-US breakdown
# .7 => 70% us
# 0 => 100% non-us
USAVN = "usavn"

DEFAULT_ISIN_PORTFOLIO_BUCKET = EQUITY_PORTFOLIO
