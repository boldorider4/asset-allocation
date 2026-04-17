import argparse
import json
import numpy as np

from asset_price import set_ignore_cache as set_ignore_cache_asset_price
from pathlib import Path
from portfolio import Portfolio


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
    equity_portfolio = Portfolio(name="equity_portfolio", positions=portfolio["equity_portfolio"])
    fixed_maturity_bond_portfolio = Portfolio(name="fixed_maturity_bond_portfolio", positions=portfolio["fixed_maturity_bond_portfolio"])
    cash_portfolio = Portfolio(name="cash_portfolio", positions=portfolio["cash_portfolio"])
    bond_portfolio = Portfolio(name="bond_portfolio", positions=portfolio["bond_portfolio"])
    commodity_portfolio = Portfolio(name="commodity_portfolio", positions=portfolio["commodity_portfolio"])

    print("*************** Equity portfolio ***************")
    # calculate developed markets vs. emerging markets allocation
    dmem_allocation = float(np.dot(equity_portfolio.position_values, equity_portfolio.dmem)) / float(np.sum(equity_portfolio.position_values))
    print('developed markets vs. emerging markets allocation: {:.2f}%'.format(dmem_allocation * 100))

    # calculate us vs. non-us allocation within developed markets
    usavn_allocation = float(np.dot(equity_portfolio.position_values, equity_portfolio.usavn)) / float(np.dot(equity_portfolio.position_values, equity_portfolio.dmem))
    print('us vs. non-us allocation within developed markets: {:.2f}%'.format(usavn_allocation * 100))
    print('equity portfolio value: {:.2f}'.format(equity_portfolio.total_value))

    print("*************** Fixed maturity bond portfolio ***************")
    print('bimmer fund: {:.2f}'.format(fixed_maturity_bond_portfolio.total_value))

    print("*************** Emergency fund portfolio ***************")
    print('emergency fund: {:.2f}'.format(cash_portfolio.total_value))

    print("*************** Total portfolio value ***************")
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