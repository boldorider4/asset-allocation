import argparse
import json
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

from asset_price import set_ignore_cache as set_ignore_cache_asset_price
from pathlib import Path
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.non_regional_portfolio import NonRegionalPortfolio


def _default_assets_path() -> Path:
    return Path(__file__).resolve().parent / "assets.json"


def load_portfolio(path: Path | None = None) -> dict[str, list[dict]]:
    """Load portfolio buckets from a JSON file (default: assets.json next to this module)."""
    assets_path = path or _default_assets_path()
    with assets_path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("assets root must be a JSON object")
    for key, positions in data.items():
        if not isinstance(positions, list):
            raise ValueError(f"{key!r} must be a JSON array")
        for i, pos in enumerate(positions):
            if not isinstance(pos, dict):
                raise ValueError(f"{key}[{i}] must be a JSON object")
    return data


def main(assets_file_path: Path | None = None):
    portfolio: dict[str, list[dict]] = load_portfolio(assets_file_path)

    # make portfolios
    equity_portfolio = RegionalPortfolio(name="Equity Portfolio", positions=portfolio["equity_portfolio"])
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="Bimmer Fund", positions=portfolio["fixed_maturity_bond_portfolio"], consolidate=True)
    cash_portfolio = NonRegionalPortfolio(name="Emergency Fund", positions=portfolio["cash_portfolio"], consolidate=True)
    bond_portfolio = RegionalPortfolio(name="Bond Portfolio", positions=portfolio["bond_portfolio"])
    non_regional_bond_portfolio = NonRegionalPortfolio(name="Bond Portfolio", positions=portfolio["bond_portfolio"], consolidate=True)
    commodity_portfolio = NonRegionalPortfolio(name="Inflation Hedge", positions=portfolio["commodity_portfolio"])

    print(equity_portfolio)
    equity_portfolio.plot_dmem()
    equity_portfolio.plot_usavn()
    equity_portfolio.plot()

    print(bond_portfolio)
    bond_portfolio.plot_dmem()
    bond_portfolio.plot_usavn()
    bond_portfolio.plot()

    print(fixed_maturity_bond_portfolio)
    fixed_maturity_bond_portfolio.plot()

    print(cash_portfolio)
    cash_portfolio.plot()

    print(commodity_portfolio)
    commodity_portfolio.plot()

    total_portfolio = equity_portfolio + non_regional_bond_portfolio + commodity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio
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
    args = parser.parse_args()
    if args.no_cache:
        set_ignore_cache_asset_price(True)
    main(args.assets_file)