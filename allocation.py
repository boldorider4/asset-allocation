import argparse
import sys

import matplotlib

# Match visual package: macosx backend cannot move windows; use TkAgg before pyplot import.
if (
    sys.platform == "darwin"
    and "matplotlib.pyplot" not in sys.modules
    and matplotlib.get_backend().lower() == "macosx"
):
    matplotlib.use("tkagg")

import numpy as np
import matplotlib.pyplot as plt

from position import set_ignore_cache, set_fetch_oskar, set_assets_file, get_assets_file
from pathlib import Path
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.non_regional_portfolio import NonRegionalPortfolio
from utils import portfolio, load_portfolio


def main():
    # Populate the module-level ``utils.portfolio`` in place so other modules
    # (e.g. ``position.factory``) that imported it see the loaded data.
    portfolio.clear()
    portfolio.update(load_portfolio(get_assets_file()))

    # make portfolios
    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=portfolio["equity_portfolio"])
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=portfolio["fixed_maturity_bond_portfolio"], consolidate=True)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=portfolio["cash_portfolio"], consolidate=True)
    bond_portfolio = RegionalPortfolio(name="Bond Portfolio", positions=portfolio["bond_portfolio"])
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bond Portfolio", positions=portfolio["bond_portfolio"], consolidate=True)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=portfolio["commodity_portfolio"])
    pension_portfolio = NonRegionalPortfolio(name="bAV", positions=portfolio["pension_portfolio"])

    print(equity_portfolio)
    equity_portfolio.plot_dmem()
    equity_portfolio.plot_usavn()
    equity_portfolio.plot()

    print(bond_portfolio)
    bond_portfolio.plot_dmem()
    bond_portfolio.plot_usavn()
    bond_portfolio.plot()

    print(fixed_maturity_bond_portfolio)
    # fixed_maturity_bond_portfolio.plot()

    print(cash_portfolio)
    # cash_portfolio.plot()

    print(commodity_portfolio)
    # commodity_portfolio.plot()

    total_growth_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio
    total_growth_portfolio.plot(title="Growth Portfolio: {:.2f} Euro".format(total_growth_portfolio.total_value), label_fontsize=7, autopct_fontsize=7)

    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + pension_portfolio
    total_portfolio.plot(title="Total Portfolio: {:.2f} Euro".format(total_portfolio.total_value), label_fontsize=7, autopct_fontsize=7)
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio allocation report.")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch fresh prices without reading cache.json; still update cache.json after fetches.",
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
    args = parser.parse_args()
    if args.no_cache:
        set_ignore_cache(True)
    if args.fetch_oskar:
        set_fetch_oskar(True)
    if args.assets_file:
        set_assets_file(args.assets_file)
    main()