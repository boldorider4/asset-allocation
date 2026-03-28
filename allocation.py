import json
import numpy as np

from asset_price import make_position
from pathlib import Path


# globals
NAME = "name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"

# developed markets vs. emerging markets breakdown
# 1 => 100% developed markets
# 0 => 100% emerging markets
DMEM = "dmem"
# us vs. non-us breakdown
# .7 => 70% us
# 0 => 100% non-us
USAVN = "usavn"


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


def position_value(security: dict) -> float:
    pos = make_position(
        isin=security.get(ISIN),
        shares=security.get(SHARES),
        value=security.get(VALUE),
        broker=security.get(BROKER),
        dmem=security.get(DMEM),
        usavn=security.get(USAVN),
    )
    return pos.value


def main():
    portfolio: dict[str, list[dict]] = load_portfolio()
    equity_portfolio = portfolio["equity_portfolio"]
    fixed_maturity_bond_portfolio = portfolio["fixed_maturity_bond_portfolio"]
    cash_portfolio = portfolio["cash_portfolio"]
    bond_portfolio = portfolio["bond_portfolio"]
    commodity_portfolio = portfolio["commodity_portfolio"]

    print("*************** Equity portfolio ***************")

    # calculate developed markets vs. emerging markets allocation
    dmem_values = [security[DMEM] for security in equity_portfolio]
    portfolio_values = [position_value(security) for security in equity_portfolio]
    dmem_allocation = float(np.dot(portfolio_values, dmem_values)) / float(np.sum(portfolio_values))
    print('developed markets vs. emerging markets allocation: {:.2f}%'.format(dmem_allocation * 100))

    # calculate us vs. non-us allocation within developed markets
    usavn_values = [security[USAVN] for security in equity_portfolio]
    usavn_allocation = float(np.dot(portfolio_values, usavn_values)) / float(np.dot(portfolio_values, dmem_values))
    print('us vs. non-us allocation within developed markets: {:.2f}%'.format(usavn_allocation * 100))

    print("*************** Fixed maturity bond portfolio ***************")
    print('bimmer fund: {:.2f}'.format(np.sum([position_value(security) for security in fixed_maturity_bond_portfolio])))

    print("*************** Emergency fund portfolio ***************")
    print('emergency fund: {:.2f}'.format(np.sum([position_value(security) for security in cash_portfolio])))

    print("*************** Total portfolio value ***************")
    print('total portfolio value: {:.2f}'.format(np.sum([position_value(security) for security in equity_portfolio + fixed_maturity_bond_portfolio + cash_portfolio + bond_portfolio + commodity_portfolio])))


if __name__ == "__main__":
    main()