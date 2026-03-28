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
