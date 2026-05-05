from abc import ABC, abstractmethod

from portfolio.portfolio import (
    Portfolio,
    DM,
    EM,
    US,
    NON_US,
    NAME,
    SHORT_NAME,
    SHARES,
    VALUE,
    BROKER,
    ISIN,
    DMEM,
    USAVN,
    DMEM_OTHER,
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


def _positions_to_dicts(portfolio: Portfolio) -> list[dict]:
    rows: list[dict] = []
    for p in portfolio._positions:
        row: dict = {ISIN: p.isin}
        if p.name is not None:
            row[NAME] = p.name
        if p._short_name is not None:
            row[SHORT_NAME] = p._short_name
        if p._shares is not None:
            row[SHARES] = p._shares
        if p._value is not None:
            row[VALUE] = p._value
        if p._broker is not None:
            row[BROKER] = p._broker
        if p._dmem is not None:
            row[DMEM] = p._dmem
        if p._usavn is not None:
            row[USAVN] = p._usavn
        if p._dmem_other is not None:
            row[DMEM_OTHER] = p._dmem_other
        rows.append(row)
    return rows


def factory(portfolio: Portfolio) -> DeallocatableInterface:
    """Return a deallocatable wrapper built from ``portfolio``'s current state."""
    name = portfolio._name
    positions = _positions_to_dicts(portfolio)
    if isinstance(portfolio, RegionalPortfolio):
        return RegionalDeallocatable(name, positions)
    return Deallocatable(name, positions)


class Deallocatable(Portfolio, DeallocatableInterface):
    def _refresh_portfolio_metrics(self) -> None:
        self._value = self._calculate_value()
        self._dmem = self._calculate_dmem()
        self._usavn = self._calculate_usavn()

    def _deallocate_value_by_name(self, name: str, value: float) -> None:
        for position in self._positions:
            if position.name == name:
                position.sell_value(value)
        self._refresh_portfolio_metrics()

    def _deallocate_value_by_isin(self, isin: str, value: float) -> None:
        for position in self._positions:
            if position.isin == isin:
                position.sell_value(value)
        self._refresh_portfolio_metrics()

    def _deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        for position in self._positions:
            if position.isin == isin:
                position.sell_shares(shares)
        self._refresh_portfolio_metrics()

    def deallocate_value_by_name(self, name: str, value: float) -> None:
        self._deallocate_value_by_name(name, value)

    def deallocate_value_by_isin(self, isin: str, value: float) -> None:
        self._deallocate_value_by_isin(isin, value)

    def deallocate_shares_by_isin(self, isin: str, shares: float) -> None:
        self._deallocate_shares_by_isin(isin, shares)


class RegionalDeallocatable(RegionalPortfolio, Deallocatable):
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
