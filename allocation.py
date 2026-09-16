import logging

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
    portfolio,
    load_portfolio,
    write_portfolio_to_file,
    get_assets_file,
    get_fetch_oskar,
    get_fetch_scalable,
    get_fetch_traderepublic,
    get_incognito,
    apply_incognito_scaling,
)
from visual import get_plotter

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


def main():
    # Populate the module-level ``utils.portfolio`` in place so other modules
    # (e.g. ``position.factory``) that imported it see the loaded data.
    assets_path = get_assets_file()
    logger.info("Loading portfolio from %s", assets_path)
    portfolio.clear()
    portfolio.update(load_portfolio(assets_path))
    if get_fetch_oskar():
        logger.info("Fetching OSKAR ETF weights from cockpit")
        update_oskar_etfs_in_portfolio()
        write_portfolio_to_file(assets_path)
        logger.info("Wrote updated portfolio to %s", assets_path)

    if get_fetch_scalable():
        logger.info("Fetching Scalable holdings from sc CLI")
        update_scalable_etfs_in_portfolio()
        write_portfolio_to_file(assets_path)
        logger.info("Wrote updated portfolio to %s", assets_path)

    if get_fetch_traderepublic():
        logger.info("Fetching Trade Republic holdings from pytr")
        update_traderepublic_etfs_in_portfolio()
        write_portfolio_to_file(assets_path)
        logger.info("Wrote updated portfolio to %s", assets_path)

    if get_incognito():
        logger.info("Incognito mode: scaling display values")
        apply_incognito_scaling()

    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=portfolio[EQUITY_PORTFOLIO])
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=portfolio[FIXED_MATURITY_BOND_PORTFOLIO], consolidate=True)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=portfolio[CASH_PORTFOLIO], consolidate=True)
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bonds", positions=portfolio[BOND_PORTFOLIO], consolidate=True)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=portfolio[COMMODITY_PORTFOLIO])
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=portfolio[PENSION_PORTFOLIO])
    persist_oskar_shares_in_portfolio()
    persist_fetched_values_in_portfolio()
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
        closing_title="Value: {:.2f} €".format(total_growth_portfolio.total_value),
        label_fontsize=7,
        autopct_fontsize=7,
    )
    total_portfolio.plot_geosplit(
        title="Complete Portfolio",
        closing_title="Net Worth: {:.2f} €".format(total_portfolio.total_value),
        label_fontsize=7,
        autopct_fontsize=7,
    )
    get_plotter().finish_plots()
