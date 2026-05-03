from asset_price.factory import factory as _factory
from asset_price.position import Position
import numpy as np


# globals
NAME = "name"
SHORT_NAME = "short_name"
SHARES = "shares"
VALUE = "value"
BROKER = "broker"
ISIN = "ISIN"

# developed markets vs. emerging markets breakdown
# 1 => 100% developed markets
# 0 => 100% emerging markets
DMEM = "dmem"
# developed markets vs. other markets breakdown when coutry listed is "other"
# 1 => 100% of "other" is considered developed markets
# 0.5 => 50% of "other" is considered developed markets
DMEM_OTHER = "dmem_other"
# us vs. non-us breakdown
# .7 => 70% us
# 0 => 100% non-us
USAVN = "usavn"


class Portfolio:
    def __init__(self, name: str, positions: list[dict] | None = None):
        self._name = name
        self._positions: list[Position] = list()
        for position in positions:
            self._positions.append(_factory(
                isin=position.get(ISIN),
                name=position.get(NAME),
                short_name=position.get(SHORT_NAME),
                shares=position.get(SHARES),
                value=position.get(VALUE),
                broker=position.get(BROKER),
                dmem=position.get(DMEM),
                usavn=position.get(USAVN),
                dmem_other=position.get(DMEM_OTHER),
            ))
        self._value = self._calculate_value()
        self._dmem = self._calculate_dmem()
        self._usavn = self._calculate_usavn()
        self._visualizer = None # to be overridden by subclasses

    def _calculate_value(self) -> float:
        return sum(position.value for position in self._positions)

    def _calculate_dmem(self) -> list[float]:
        return [position.dmem for position in self._positions]

    def _calculate_usavn(self) -> list[float]:
        return [position.usavn for position in self._positions]

    @property
    def value(self) -> float:
        return self._value
    
    @property
    def dmem(self) -> list[float] | None:
        return self._dmem
    
    @property
    def usavn(self) -> list[float] | None:
        return self._usavn

    @property
    def total_value(self) -> float:
        return self._value

    def __str__(self) -> str:
        dmem_nonzero = [x for x in self._dmem if x is not None] if self._dmem is not None else None
        dmem_mean = np.mean(dmem_nonzero) if dmem_nonzero else 0

        usavn_nonzero = [x for x in self._usavn if x is not None] if self._usavn is not None else None
        usavn_mean = np.mean(usavn_nonzero) if usavn_nonzero else 0

        return (
            f"*************** Portfolio: {self._name} ***************\n"
            f"Value: {self._value:.2f} \n"
            f"DMEM: {dmem_mean * 100:.2f}% \n"
            f"USAVN: {usavn_mean * 100:.2f}% \n"
            f"Positions:\n"
            f"{''.join(str(position) for position in self._positions)}"
        )


if __name__ == "__main__":
    portfolio = Portfolio(name="portfolio", positions=[
        # Amundi Equity World UCITS ETF (Acc)
        {
            ISIN: "IE000BI8OT95",
            SHARES: 100,
            VALUE: 10000,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 0.7,
            DMEM_OTHER: 1,
        },
        # Scalable AC World Xtrackers UCITS ETF (Acc)
        {
            ISIN: "LU2903252349",
            SHARES: 133,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 0.88,
            USAVN: 0.625,
            DMEM_OTHER: 0.5,
        },
        # iShares Core MSCI EM IMI UCITS ETF (Acc)
        {
            ISIN: "IE00BKM4GZ66",
            SHARES: 78,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 0,
            USAVN: 0,
            DMEM_OTHER: 0,
        },
        # State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF (Acc)
        {
            ISIN: "IE00B4YBJ215",
            SHARES: 80,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 1,
            DMEM_OTHER: 1,
        },
        # iShares US Treasury Bond 1-3Y Aggregate EUR Hedged ETF
        {
            ISIN: "IE00BDFK1573",
            SHARES: 51,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 1,
            DMEM_OTHER: 0.7,
        },
        # Xtrackers II EUR Overnight Rate Swap UCITS ETF
        {
            ISIN: "LU0290358497",
            SHARES: 50,
            VALUE: None,
            BROKER: "scalable",
            DMEM: 1,
            USAVN: 0,
            DMEM_OTHER: 0.8,
        },
    ])
    print(portfolio)