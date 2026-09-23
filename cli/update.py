# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cli.common import (
    BOND_PORTFOLIO,
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
    FIXED_MATURITY_BOND_PORTFOLIO,
    PENSION_PORTFOLIO,
)
from cli.logger import attach_color_stderr_handler_for_module
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
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


def main(ctx: RuntimeContext) -> None:
    """Run the update pipeline against an explicit ``RuntimeContext``."""
    if ctx is None:
        raise TypeError("cli.update.main requires an explicit RuntimeContext (ctx)")
    ctx.load_portfolio()
    ctx.ensure_cache_loaded()
    ctx.configure_web_output()
    logger.info("Loading portfolio from %s", ctx.config.assets_file)
    # Broker scrapes run FIRST
    # Everything composed below is built from final holdings
    if ctx.config.fetch_oskar:
        logger.info("Fetching OSKAR ETF weights from cockpit")
        update_oskar_etfs_in_portfolio(ctx)
        ctx.persist_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.fetch_scalable:
        logger.info("Fetching Scalable holdings from sc CLI")
        update_scalable_etfs_in_portfolio(ctx)
        ctx.persist_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.fetch_traderepublic:
        logger.info("Fetching Trade Republic holdings from pytr")
        update_traderepublic_etfs_in_portfolio(ctx)
        ctx.persist_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    # Composition from the final dicts: the factory stages cache backfills
    # and estimates OSKAR shares from fresh scrape values, queuing them for the persists below.
    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=ctx.portfolio[EQUITY_PORTFOLIO], ctx=ctx)
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=ctx.portfolio[FIXED_MATURITY_BOND_PORTFOLIO], ctx=ctx)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=ctx.portfolio[CASH_PORTFOLIO], ctx=ctx)
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bonds", positions=ctx.portfolio[BOND_PORTFOLIO], ctx=ctx)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=ctx.portfolio[COMMODITY_PORTFOLIO], ctx=ctx)
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=ctx.portfolio[PENSION_PORTFOLIO], ctx=ctx)

    # The persists consume the factory-queued pending writes
    persist_oskar_shares_in_portfolio(ctx)
    persist_fetched_values_in_portfolio(ctx)

    logger.info("Computing incognito display factor")
    apply_incognito_scaling(ctx)

    # Sector and geo splits were calculated once at composition above from
    # trusted staged splits (or freshly scraped ones when the fetch flags are on)
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
            title="95-5 Equity Portfolio: Geo Breakdown",
            closing_title="Total Value: {tot_value}",
            label_fontsize=7,
            autopct_fontsize=7,
            incognito=incognito,
        )
        total_growth_portfolio.plot_sectors(
            title="95-5 Equity Portfolio: Sector Breakdown",
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
