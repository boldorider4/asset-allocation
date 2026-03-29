from asset_price.factory import factory as _factory
import numpy as np


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
        self._positions = list()
        for position in positions:
            self._positions.append(_factory(
                isin=position.get(ISIN),
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
    def dmem(self) -> list[float]:
        return self._dmem
    
    @property
    def usavn(self) -> list[float]:
        return self._usavn

    @property
    def position_values(self) -> list[float]:
        return [position.value for position in self._positions]

    @property
    def total_value(self) -> float:
        return np.sum(self.position_values)

    def __str__(self) -> str:
        return (
            f"*************** Portfolio: {self._name} ***************\n"
            f"Value: {self._calculate_value():.2f} \n"
            f"DMEM: {self._calculate_dmem()} \n"
            f"USAVN: {self._calculate_usavn()} \n"
            f"Positions:\n"
            f"{''.join(str(position) for position in self._positions)}\n"
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
    ])
    print(portfolio)