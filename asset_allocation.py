import numpy as np

from position import make_position

# globals
NAME = "name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"
SCALABLE = "scalable"
OSKAR = "oskar"
TFBANK = "tfbank"

# developed markets vs. emerging markets breakdown
# 1 => 100% developed markets
# 0 => 100% emerging markets
DMEM = "dmem"
# us vs. non-us breakdown
# .7 => 70% us
# 0 => 100% non-us
USAVN = "usavn"


def position_value(security: dict) -> float:
    sh = security.get(SHARES)
    isin = security.get(ISIN)
    if sh is not None:
        if not isin:
            raise ValueError(
                f"Missing ISIN for position with shares: {security.get(NAME)}"
            )
        pos = make_position(isin)
        return float(sh) * pos.last_price()
    val = security.get(VALUE)
    if val is not None:
        return float(val)
    raise ValueError(f"Cannot value position: {security.get(NAME)}")


### Pillar 3 portfolio composition

# equity portfolio composition
equity_portfolio = [
    { NAME: "Sample Global Developed Equity 1",
      SHARES: None,
      VALUE: 45000,
      BROKER: SCALABLE,
      ISIN: "ZZ1000000001",
      DMEM: 1,
      USAVN: 0.65 },
    { NAME: "Sample Emerging Markets Broad 2",
      SHARES: None,
      VALUE: 12000,
      BROKER: SCALABLE,
      ISIN: "ZZ1000000002",
      DMEM: 0,
      USAVN: 0},
    { NAME: "Sample US Large Cap Screened 15",
      VALUE: 16500,
      BROKER: OSKAR,
      ISIN: "ZZ1000000015",
      DMEM: 1,
      USAVN: 1},
    ]

# bond portfolio composition
bond_portfolio = [
    { NAME: "Sample Aggregate Bond Hedged 1",
      VALUE: 3000,
      BROKER: OSKAR,
      ISIN: "ZZ2000000001",
      DMEM: 0.85,
      USAVN: 0.6},
    ]

# commodity and inflation-linked bond portfolio composition
commodity_portfolio = [
    { NAME: "Sample Commodity Tracker A",
      VALUE: 3500,
      BROKER: SCALABLE,
      ISIN: "ZZ3000000001",
      DMEM: 0.85,
      USAVN: 0.6},
    ]

### Pillar 2 portfolio composition

# fixed maturity bond portfolio composition (bimmer fund)
fixed_maturity_bond_portfolio = [
    { NAME: "Sample Short Corporate 1",
      VALUE: 2800,
      BROKER: SCALABLE,
      ISIN: "ZZ4000000001",
      DMEM: 1,
      USAVN: 0},
    ]

### Pillar 1 portfolio composition

# cash portfolio composition
cash_portfolio = [
    { NAME: "Sample Cash Account 1",
      VALUE: 25,
      BROKER: SCALABLE,
      ISIN: None,
      DMEM: 0,
      USAVN: 0},
    { NAME: "Sample Money-Market ETF 1",
      VALUE: 13000,
      BROKER: SCALABLE,
      ISIN: "ZZ5000000001",
      DMEM: 1,
      USAVN: 0},
    ]

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