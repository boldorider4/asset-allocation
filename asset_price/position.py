"""
Position pricing: abstract base plus Yahoo Finance and JustETF implementations.
"""

from __future__ import annotations
from abc import ABC, abstractmethod

class Position(ABC):
    """ISIN-based price lookup: fast quote vs last historical close."""

    def __init__(
        self, isin: str,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        last_price: float | None = None,
    ) -> None:
        self._shares = shares
        self._value = value
        self._broker = broker
        self._isin = isin
        self._dmem = dmem
        self._usavn = usavn
        # the logic is: if last_price is provided, it means it was cached
        if last_price is not None:
            self._last_price = last_price
        # if not, let's try and determine it from the ISIN
        elif self._isin is not None:
            self._last_price = self._fast_info_price()
            # if the fetch was unsuccessful
            if self._last_price is None:
                raise RuntimeError(f"No fast/quote price for ISIN {self._isin}")
        # otherwise, no price is cached or determined if only the value of the position is provided
        elif self._value is None:
            raise RuntimeError(f"No last price for position because neither value nor ISIN was provided")

    @property
    def isin(self) -> str:
        return self._isin

    @property
    def value(self) -> float | None:
        if self._value is not None:
            return self._value
        if self._shares is not None:
            return self._shares * self._last_price
        return None

    @property
    def dmem(self) -> float | None:
        return self._dmem

    @property
    def usavn(self) -> float | None:
        return self._usavn

    @property
    def last_price(self) -> float | None:
        return self._last_price

    def price_history(self) -> float:
        p = self._history_last_close()
        if p is None:
            raise RuntimeError(f"No historical close for ISIN {self._isin}")
        return float(p)

    @abstractmethod
    def _fast_info_price(self) -> float | None:
        """Current/quick price (e.g. Yahoo fast_info or JustETF latestQuote)."""
        ...

    @abstractmethod
    def _history_last_close(self) -> float | None:
        """Last available daily close from history/chart series."""
        ...
