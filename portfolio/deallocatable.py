from abc import ABC, abstractmethod

from portfolio.portfolio import (
    Portfolio,
    DM,
    EM,
    US,
    NON_US
)
from portfolio.regional_portfolio import RegionalPortfolio


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


def factory(portfolio: Portfolio) -> DeallocatableInterface:
    """Return a deallocatable wrapper built from ``portfolio``'s current state."""
    if isinstance(portfolio, RegionalPortfolio):
        return RegionalDeallocatable(portfolio)
    return Deallocatable(portfolio)


class Deallocatable(DeallocatableInterface):
    def __init__(self, portfolio: Portfolio):
        self._portfolio = portfolio

    def _refresh_portfolio_metrics(self) -> None:
        self._portfolio._value = self._portfolio._calculate_value()
        self._portfolio._dmem = self._portfolio._calculate_dmem()
        self._portfolio._usavn = self._portfolio._calculate_usavn()

    def _deallocate_value_by_name(self, name: str, value: float) -> None:
        for position in self._portfolio._positions:
            if position.name == name:
                position.sell_value(value)
        self._refresh_portfolio_metrics()

    def _deallocate_value_by_isin(self, isin: str, value: float) -> None:
        for position in self._portfolio._positions:
            if position.isin == isin:
                position.sell_value(value)
        self._refresh_portfolio_metrics()

    def _deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        for position in self._portfolio._positions:
            if position.isin == isin:
                position.sell_shares(shares)
        self._refresh_portfolio_metrics()

    def deallocate_value_by_name(self, name: str, value: float) -> None:
        self._deallocate_value_by_name(name, value)

    def deallocate_value_by_isin(self, isin: str, value: float) -> None:
        self._deallocate_value_by_isin(isin, value)

    def deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        self._deallocate_shares_by_isin(isin, shares)


class RegionalDeallocatable(Deallocatable):
    def __init__(self, portfolio: Portfolio):
        super().__init__(portfolio)

    def deallocate_value_by_region(self, region: str, value: float) -> None:
        for position in self._positions:
            sale_factor = 0.0
            dmem = position.dmem if position.dmem is not None else 0.0
            usavn = position.usavn if position.usavn is not None else 0.0
            if region == DM:
                sale_factor = dmem
            elif region == EM:
                sale_factor = 1.0 - dmem
            elif region == US:
                sale_factor = usavn * dmem
            elif region == NON_US:
                sale_factor = (1.0 - usavn) * dmem
            if sale_factor > 0:
                position.sell_value(value * sale_factor)
        self._refresh_portfolio_metrics()
