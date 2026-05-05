from abc import ABC, abstractmethod

from portfolio.portfolio import Portfolio
from portfolio.regional_portfolio import RegionalPortfolio
from portfolio.portfolio import DM, EM, US, NON_US


class DeallocatableInterface(ABC):
    @abstractmethod
    def deallocate_value_by_name(self, name: str, value: float) -> None:
        ...

    @abstractmethod
    def deallocate_value_by_isin(self, isin: str, value: float) -> None:
        ...

    @abstractmethod
    def deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        ...


def _deallocate_value_by_name(portfolio: Portfolio, name: str, value: float) -> None:
    for position in portfolio._positions:
        if position.name == name:
            portfolio._positions.sell_value(value)
    portfolio._calculate_value()
    portfolio._calculate_dmem()
    portfolio._calculate_usavn()


def _deallocate_value_by_isin(portfolio: Portfolio, isin: str, value: float) -> None:
    for position in portfolio._positions:
        if position.isin == isin:
            portfolio._positions.sell_value(value)
    portfolio._calculate_value()
    portfolio._calculate_dmem()
    portfolio._calculate_usavn()


def _deallocate_shares_by_isin(portfolio: Portfolio, isin: str, shares: float) -> None:
    for position in portfolio._positions:
        if position.isin == isin:
            portfolio._positions.sell_shares(shares)
    portfolio._calculate_value()
    portfolio._calculate_dmem()
    portfolio._calculate_usavn()


class Deallocatable(Portfolio, DeallocatableInterface):
    def __init__(self, name: str, positions: list[dict]):
        super().__init__(name, positions)

    def deallocate_value_by_name(self, name: str, value: float) -> None:
        _deallocate_value_by_name(self, name, value)

    def deallocate_value_by_isin(self, isin: str, value: float) -> None:
        _deallocate_value_by_isin(self, isin, value)

    def deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        _deallocate_shares_by_isin(self, isin, shares)


class RegionalDeallocatable(RegionalPortfolio, DeallocatableInterface):
    def __init__(self, name: str, positions: list[dict]):
        super().__init__(name, positions)

    def deallocate_value_by_name(self, name: str, value: float) -> None:
        _deallocate_value_by_name(self, name, value)

    def deallocate_value_by_isin(self, isin: str, value: float) -> None:
        _deallocate_value_by_isin(self, isin, value)

    def deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        _deallocate_shares_by_isin(self, isin, shares)

    def deallocate_value_by_region(self, region: str, value: float) -> None:
        for position in self._positions:
            sale_factor = 0
            if region == DM:
                sale_factor = position.dmem
            elif region == EM:
                sale_factor = 1 - position.dmem
            elif region == US:
                sale_factor = position.usavn * position.dmem
            elif region == NON_US:
                sale_factor = (1 - position.usavn) * position.dmem
            if sale_factor > 0:
                self._positions.sell_value(value * sale_factor)
