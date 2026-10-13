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
from position.justetf_position import JustETFPosition
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.non_regional_portfolio import NonRegionalPortfolio
from scrape.oskar import _ISIN_STRICT, _OSKAR, update_oskar_etfs_in_portfolio
from scrape.scalable import update_scalable_etfs_in_portfolio
from scrape.traderepublic import update_traderepublic_etfs_in_portfolio
from utils import (
    persist_fetched_values_in_portfolio,
    persist_oskar_shares_in_portfolio,
    save_position_in_cache,
)

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


def _estimate_oskar_shares_with_fresh_quotes(ctx: RuntimeContext) -> int:
    """Re-estimate OSKAR shares as fresh value / fresh quote, in place.

    Only for ``--fetch-oskar`` without ``--fetch-prices`` (otherwise the
    factory estimates once through the normal price path, and without an
    OSKAR scrape there is nothing new to estimate from). Fetches one
    JustETF quote per OSKAR ISIN, caches it (so composition later reuses
    it instead of refetching), and writes shares straight into the
    portfolio rows; the caller's ``persist_portfolio`` writes them out.
    Rows without an ISIN (Tagesgeld/cash), without a value, or whose
    quote fetch fails are skipped with a warning and never abort the run.
    """
    if not ctx.config.fetch_oskar or ctx.config.fetch_prices:
        return 0
    count = 0
    for positions in ctx.portfolio.values():
        for position in positions:
            if (position.get("broker") or position.get("Broker")) != _OSKAR:
                continue
            isin = position.get("ISIN") or position.get("isin")
            if not isin or not _ISIN_STRICT.match(str(isin)):
                continue
            value = position.get("value")
            if value is None:
                continue
            try:
                price = JustETFPosition(str(isin), ctx=ctx)._fast_info_price()
            except (RuntimeError, OSError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping OSKAR share estimate for %s: quote fetch failed (%s)",
                    isin,
                    exc,
                )
                continue
            if not price:
                logger.warning(
                    "Skipping OSKAR share estimate for %s: no quote available",
                    isin,
                )
                continue
            save_position_in_cache(ctx, str(isin), price=price, update_price=True)
            position["shares"] = float(value) / float(price)
            count += 1
    return count


def main(ctx: RuntimeContext) -> None:
    """Run the update pipeline against an explicit ``RuntimeContext``."""
    if ctx is None:
        raise TypeError("cli.update.main requires an explicit RuntimeContext (ctx)")
    ctx.load_portfolio()
    ctx.ensure_cache_loaded()
    logger.info("Loading portfolio from %s", ctx.config.assets_file)
    # Broker scrapes run FIRST
    # Everything composed below is built from final holdings
    if ctx.config.fetch_oskar:
        logger.info("Fetching OSKAR ETF weights from cockpit")
        update_oskar_etfs_in_portfolio(ctx)
        refreshed = _estimate_oskar_shares_with_fresh_quotes(ctx)
        if refreshed:
            logger.info(
                "Re-estimated shares for %d OSKAR position(s)", refreshed
            )
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

    # Sector and geo splits were calculated once at composition above from
    # trusted staged splits (or freshly scraped ones when the fetch flags are on)
    total_growth_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio
    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + pension_portfolio

    output_dir = ctx.output_data_dir()
    plotter = ctx.plotter_class()
    if hasattr(plotter, "data_dir"):
        plotter.data_dir = output_dir
        plotter._slug_counts = {}
        plotter._plot_seq = 0
    logger.info("Writing charts to %s", output_dir)
    total_growth_portfolio.plot_geosplit(
        title="95-5 Equity Portfolio: Geo Breakdown",
        closing_title="Total Value: {tot_value}",
        label_fontsize=7,
        autopct_fontsize=7,
    )
    total_growth_portfolio.plot_sectors(
        title="95-5 Equity Portfolio: Sector Breakdown",
        label_fontsize=7,
        autopct_fontsize=7,
    )
    total_portfolio.plot_geosplit(
        title="Complete Portfolio",
        closing_title="Net Worth: {tot_value}",
        label_fontsize=7,
        autopct_fontsize=7,
    )
    ctx.flush_cache()
    ctx.flush_isin_registry()
    ctx.plotter_class().finish_plots()
