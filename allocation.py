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
    logger.info("Writing charts to %s", ctx.output_data_dir)
    if ctx.config.fetch_oskar:
        logger.info("Fetching OSKAR ETF weights from cockpit")
        update_oskar_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.fetch_scalable:
        logger.info("Fetching Scalable holdings from sc CLI")
        update_scalable_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.fetch_traderepublic:
        logger.info("Fetching Trade Republic holdings from pytr")
        update_traderepublic_etfs_in_portfolio(ctx)
        ctx.flush_portfolio()
        logger.info("Wrote updated portfolio to %s", ctx.config.assets_file)

    if ctx.config.incognito:
        logger.info("Incognito mode: scaling display values")
        apply_incognito_scaling(ctx)

    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=ctx.portfolio[EQUITY_PORTFOLIO], ctx=ctx)
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=ctx.portfolio[FIXED_MATURITY_BOND_PORTFOLIO], consolidate=True, ctx=ctx)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=ctx.portfolio[CASH_PORTFOLIO], consolidate=True, ctx=ctx)
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bonds", positions=ctx.portfolio[BOND_PORTFOLIO], consolidate=True, ctx=ctx)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=ctx.portfolio[COMMODITY_PORTFOLIO], ctx=ctx)
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=ctx.portfolio[PENSION_PORTFOLIO], ctx=ctx)
    persist_oskar_shares_in_portfolio(ctx)
    persist_fetched_values_in_portfolio(ctx)
    total_growth_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio
    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + pension_portfolio

    total_growth_portfolio.plot_geosplit(
        title="95-5 Equity Portfolio",
        closing_title="Value: {:.2f} €".format(total_growth_portfolio.total_value),
        label_fontsize=7,
        autopct_fontsize=7,
    )
    total_growth_portfolio.plot_sectors(
        title="Sector Breakdown",
        label_fontsize=7,
        autopct_fontsize=7,
    )
    total_portfolio.plot_geosplit(
        title="Complete Portfolio",
        closing_title="Net Worth: {:.2f} €".format(total_portfolio.total_value),
        label_fontsize=7,
        autopct_fontsize=7,
    )
    ctx.flush_cache()
    ctx.plotter_class().finish_plots()
