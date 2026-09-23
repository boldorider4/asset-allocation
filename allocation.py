# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from common import (
    BOND_PORTFOLIO,
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
    FIXED_MATURITY_BOND_PORTFOLIO,
    PENSION_PORTFOLIO,
)
from logger import attach_color_stderr_handler_for_module
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.non_regional_portfolio import NonRegionalPortfolio
from scrape.oskar import update_oskar_etfs_in_portfolio
from scrape.scalable import update_scalable_etfs_in_portfolio
from scrape.traderepublic import update_traderepublic_etfs_in_portfolio
from utils import (
    persist_fetched_values_in_portfolio,
    persist_oskar_shares_in_portfolio,
    apply_incognito_scaling,
)

if TYPE_CHECKING:
    from context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


def main(ctx: RuntimeContext) -> None:
    """Run the update pipeline against an explicit ``RuntimeContext``."""
    if ctx is None:
        raise TypeError("allocation.main requires an explicit RuntimeContext (ctx)")
    ctx.load_portfolio()
    ctx.ensure_cache_loaded()
    ctx.configure_web_output()
    logger.info("Loading portfolio from %s", ctx.config.assets_file)
    # Create Portfolio objects first (needed for cache invalidation after updates)
    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=ctx.portfolio[EQUITY_PORTFOLIO], ctx=ctx)
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=ctx.portfolio[FIXED_MATURITY_BOND_PORTFOLIO], ctx=ctx)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=ctx.portfolio[CASH_PORTFOLIO], ctx=ctx)
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bonds", positions=ctx.portfolio[BOND_PORTFOLIO], ctx=ctx)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=ctx.portfolio[COMMODITY_PORTFOLIO], ctx=ctx)
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=ctx.portfolio[PENSION_PORTFOLIO], ctx=ctx)

    if ctx.config.fetch_oskar:
        logger.info("Fetching OSKAR ETF weights from cockpit")
        update_oskar_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.fetch_scalable:
        logger.info("Fetching Scalable holdings from sc CLI")
        updated_isins = update_scalable_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)
        # Invalidate staged sectors on affected Position objects and
        # refresh Portfolio sector aggregation — but only what was NOT
        # freshly scraped this run. With --fetch-geosplit/--fetch-sectorsplit
        # the factory already built these objects from fresh splits, so
        # invalidating would just burn a redundant network round-trip per
        # ISIN (and risk empty rows on fetch failure). Countries are never
        # invalidated: they are construction-time values with no refresh
        # path by design (their store side stays gated in the scraper).
        if not ctx.config.fetch_geosplit or not ctx.config.fetch_sectorsplit:
            for portfolio in [
                equity_portfolio,
                fixed_maturity_bond_portfolio,
                cash_portfolio,
                non_regional_bond_portfolio,
                commodity_portfolio,
                pension_portfolio,
            ]:
                for position in portfolio._positions:
                    if position.isin in updated_isins:
                        position.invalidate_sectors()
                portfolio.refresh_sectors()

    if ctx.config.fetch_traderepublic:
        logger.info("Fetching Trade Republic holdings from pytr")
        update_traderepublic_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    logger.info("Computing incognito display factor")
    apply_incognito_scaling(ctx)

    persist_oskar_shares_in_portfolio(ctx)
    persist_fetched_values_in_portfolio(ctx)
    total_growth_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio
    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + pension_portfolio

    passes = [
        incognito
        for incognito, requested in (
            (False, ctx.config.plot_clear),
            (True, ctx.config.plot_incognito),
        )
        if requested
    ]
    for incognito in passes:
        ctx.configure_web_output(incognito=incognito)
        logger.info("Writing charts to %s", ctx.output_data_dir(incognito=incognito))
        total_growth_portfolio.plot_geosplit(
            title="95-5 Equity Portfolio",
            closing_title="Total Value: {tot_value}",
            label_fontsize=7,
            autopct_fontsize=7,
            incognito=incognito,
        )
        total_growth_portfolio.plot_sectors(
            title="Sector Breakdown",
            label_fontsize=7,
            autopct_fontsize=7,
            incognito=incognito,
        )
        total_portfolio.plot_geosplit(
            title="Complete Portfolio",
            closing_title="Net Worth: {tot_value}",
            label_fontsize=7,
            autopct_fontsize=7,
            incognito=incognito,
        )
    ctx.flush_cache()
    ctx.plotter_class().finish_plots()
