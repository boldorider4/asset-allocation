# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared constants (portfolio bucket keys, position field keys, etc.)."""

EQUITY_PORTFOLIO = "equity_portfolio"
FIXED_MATURITY_BOND_PORTFOLIO = "fixed_maturity_bond_portfolio"
CASH_PORTFOLIO = "cash_portfolio"
BOND_PORTFOLIO = "bond_portfolio"
COMMODITY_PORTFOLIO = "commodity_portfolio"
PENSION_PORTFOLIO = "pension_portfolio"

# Canonical asset-position field keys: single source of truth for row
# dicts in assets files, scrape appends, portfolios, and the web backend.
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
