# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared constants (portfolio bucket keys, position field keys, etc.)."""

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
ASSET_BUCKETS: tuple[tuple[str, str], ...] = (
    (EQUITY_PORTFOLIO, "Equity"),
    (BOND_PORTFOLIO, "Fixed Income"),
    (COMMODITY_PORTFOLIO, "Commodities / Inflation"),
    (CRYPTO_PORTFOLIO, "Cryptocurrencies"),
    (FIXED_MATURITY_BOND_PORTFOLIO, "Fixed Maturity"),
    (CASH_PORTFOLIO, "Cash"),
    (PENSION_PORTFOLIO, "Annuities"),
    (REAL_ESTATE_PORTFOLIO, "Real Estate Equity"),
    (PRIVATE_EQUITY_PORTFOLIO, "Private Equity"),
)
ASSET_BUCKET_KEYS: frozenset[str] = frozenset(
    key for key, _ in ASSET_BUCKETS
)

# Canonical broker literals: single source of truth for broker ids in
# scrapes (data labels), factory switch logic, and the constituents UI.
# The two manual-kind brokers are not connected to any scrape: the
# realestate broker is forced on Real Estate Equity entries, and the
# cryptowallet broker is offered in the add-row dropdown only inside
# the Cryptocurrencies section (see BROKER_SECTIONS).
OSKAR = "oskar"
SCALABLE = "scalable"
TRADEREPUBLIC = "traderepublic"
CHECK24 = "check24"
ALTE_LEIPZIGER = "alte-leipziger"
CRYPTOWALLET = "cryptowallet"
REALESTATE = "realestate"

#: Broker id -> icon filename served from frontend/icons/.
BROKER_ICONS: dict[str, str] = {
    OSKAR: "oskar.png",
    SCALABLE: "scalable.png",
    TRADEREPUBLIC: "traderepublic.png",
    CHECK24: "check24.png",
    ALTE_LEIPZIGER: "alte-leipziger.png",
    CRYPTOWALLET: "cryptowallet.svg",
    REALESTATE: "realestate.svg",
}

#: Sections a broker is offered in (add-row dropdown); keys missing from
#: this mapping are offered in every section. REALESTATE only appears
#: where it is also forced; CRYPTOWALLET only in the crypto section.
BROKER_SECTIONS: dict[str, frozenset[str]] = {
    CRYPTOWALLET: frozenset([CRYPTO_PORTFOLIO]),
    REALESTATE: frozenset([REAL_ESTATE_PORTFOLIO]),
}

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
