"""
Position pricing: abstract base plus Yahoo Finance and JustETF implementations.
"""

from __future__ import annotations
from abc import ABC, abstractmethod

class Position(ABC):
    """ISIN-based price lookup: fast quote vs last historical close."""

    def __init__(self, isin: str) -> None:
        self._isin = isin

    @property
    def isin(self) -> str:
        return self._isin

    def last_price(self) -> float:
        p = self._fast_info_price()
        if p is None:
            raise RuntimeError(f"No fast/quote price for ISIN {self._isin}")
        return float(p)

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
