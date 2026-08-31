import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import matplotlib

# Match visual package: macosx backend cannot move windows; use TkAgg before pyplot import.
if sys.platform == "darwin" and matplotlib.get_backend().lower() == "macosx":
    matplotlib.use("tkagg")

import numpy as np
import matplotlib.pyplot as plt

from logger import configure_cli_logging
from common import (
    BOND_PORTFOLIO,
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
    FIXED_MATURITY_BOND_PORTFOLIO,
    PENSION_PORTFOLIO,
)
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.non_regional_portfolio import NonRegionalPortfolio
from utils import (
    portfolio,
    load_portfolio,
    write_portfolio_to_file,
    set_fetch_prices,
    set_fetch_geosplit,
    set_fetch_oskar,
    set_assets_file,
    set_incognito,
    get_assets_file,
    get_fetch_oskar,
    get_fetch_scalable,
    get_fetch_traderepublic,
    get_incognito,
    apply_incognito_scaling,
    set_fetch_scalable,
    set_fetch_traderepublic,
)
from logger import attach_color_stderr_handler_for_module
from oskar import update_oskar_etfs_in_portfolio
from scalable import update_scalable_etfs_in_portfolio
from traderepublic import update_traderepublic_etfs_in_portfolio


def _package_version() -> str:
    try:
        return version("asset-allocation")
    except PackageNotFoundError:
        print(
            "error: asset-allocation is not installed; run `pip install -e .` from the project root",
            file=sys.stderr,
        )
        sys.exit(1)


__version__ = _package_version()

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

def main():
    # Populate the module-level ``utils.portfolio`` in place so other modules
    # (e.g. ``position.factory``) that imported it see the loaded data.
    assets_path = get_assets_file()
    logger.info("Loading portfolio from %s", assets_path)
    portfolio.clear()
    portfolio.update(load_portfolio(assets_path))
    # update the oskar etfs in the portfolio
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

    # make portfolios
    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=portfolio[EQUITY_PORTFOLIO])
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=portfolio[FIXED_MATURITY_BOND_PORTFOLIO], consolidate=True)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=portfolio[CASH_PORTFOLIO], consolidate=True)
    bond_portfolio = RegionalPortfolio(name="Bond Portfolio", positions=portfolio[BOND_PORTFOLIO])
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bond Portfolio", positions=portfolio[BOND_PORTFOLIO], consolidate=True)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=portfolio[COMMODITY_PORTFOLIO])
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=portfolio[PENSION_PORTFOLIO])
    total_growth_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio
    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + pension_portfolio

    # print(equity_portfolio)
    # equity_portfolio.plot_dmem()
    # equity_portfolio.plot_usavn()
    equity_portfolio.plot()

    # print(bond_portfolio)
    # bond_portfolio.plot_dmem()
    # bond_portfolio.plot_usavn()
    # bond_portfolio.plot()

    # print(fixed_maturity_bond_portfolio)
    # fixed_maturity_bond_portfolio.plot()

    # print(cash_portfolio)
    # cash_portfolio.plot()

    # print(commodity_portfolio)
    # commodity_portfolio.plot()

    total_growth_portfolio.plot(title="Hedged Equity Portfolio: {:.2f} Euro".format(total_growth_portfolio.total_value), label_fontsize=7, autopct_fontsize=7)
    total_portfolio.plot(title="Total Net Worth: {:.2f} Euro".format(total_portfolio.total_value), label_fontsize=7, autopct_fontsize=7)
    logger.info("Opening chart window (close window to exit)")
    plt.show()


def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Portfolio allocation report.",
        epilog=f"version {__version__}",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--fetch-prices",
        action="store_true",
        help=(
            "Scrape JustETF/Yahoo quotes and write price to cache.json. "
            "Scalable- and Trade Republic-broker rows from the assets file "
            "are included unless --fetch-scalable / --fetch-traderepublic is also set."
        ),
    )
    parser.add_argument(
        "--fetch-geosplit",
        action="store_true",
        help=(
            "Scrape country allocations (JustETF) and write them to cache.json. "
            "Without this flag, country weights are read from cache."
        ),
    )
    parser.add_argument(
        "--assets-file",
        type=Path,
        help="Path to the assets JSON file.",
    )
    parser.add_argument(
        "--fetch-oskar",
        action="store_true",
        help="Log into Oskar and scrape ETF positions.",
    )
    parser.add_argument(
        "--fetch-scalable",
        action="store_true",
        help="Log into Scalable via sc and scrape broker holdings.",
    )
    parser.add_argument(
        "--fetch-tr",
        action="store_true",
        help="Log into Trade Republic via pytr and scrape broker holdings.",
    )
    parser.add_argument(
        "--incognito",
        action="store_true",
        help="Show fake values for asset allocation.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level for stderr (default: INFO). Use DEBUG for verbose OSKAR steps.",
    )
    args = parser.parse_args()
    configure_cli_logging(getattr(logging, args.log_level))
    if args.fetch_prices:
        set_fetch_prices(True)
    if args.fetch_geosplit:
        set_fetch_geosplit(True)
    if args.fetch_oskar:
        set_fetch_oskar(True)
    if args.fetch_scalable:
        set_fetch_scalable(True)
    if args.fetch_tr:
        set_fetch_traderepublic(True)
    if args.assets_file:
        set_assets_file(args.assets_file)
    if args.incognito:
        set_incognito(True)
    main()


if __name__ == "__main__":
    cli()
