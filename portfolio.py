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