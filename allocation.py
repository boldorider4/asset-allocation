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
    equity_portfolio = RegionalPortfolio(name="equity_portfolio", positions=portfolio["equity_portfolio"])
    fixed_maturity_bond_portfolio = NonRegionalPortfolio(name="fixed_maturity_bond_portfolio", positions=portfolio["fixed_maturity_bond_portfolio"])
    cash_portfolio = NonRegionalPortfolio(name="cash_portfolio", positions=portfolio["cash_portfolio"])
    bond_portfolio = RegionalPortfolio(name="bond_portfolio", positions=portfolio["bond_portfolio"])
    commodity_portfolio = NonRegionalPortfolio(name="commodity_portfolio", positions=portfolio["commodity_portfolio"])

    print(equity_portfolio)
    equity_portfolio.plot_dmem()
    equity_portfolio.plot_usavn()
    equity_portfolio.plot_regional_split()

    print(fixed_maturity_bond_portfolio)
    print('bimmer fund: {:.2f}\n\n'.format(fixed_maturity_bond_portfolio.total_value))

    print(cash_portfolio)
    cash_portfolio.plot()
    plt.show()

    print(commodity_portfolio)
    print('commodity portfolio value: {:.2f}\n\n'.format(commodity_portfolio.total_value))

    print('total portfolio value: {:.2f}'.format(
        equity_portfolio.total_value +
        fixed_maturity_bond_portfolio.total_value +
        cash_portfolio.total_value +
        bond_portfolio.total_value +
        commodity_portfolio.total_value))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio allocation report.")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch fresh prices; do not read or write cache.json.",
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