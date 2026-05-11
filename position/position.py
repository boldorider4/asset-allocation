"""
Position pricing: abstract base plus Yahoo Finance and JustETF implementations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from logger import attach_color_stderr_handler_for_module

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


_US_MARKET_NAME = "United States"
_OTHER_MARKET_NAME = "Other"

# MSCI-style developed economies; English names as used on JustETF / common data feeds.
# Offshore / crown dependencies included when they appear in ETF country breakdowns.
_LIST_OF_DEVELOPED_MARKETS = [
    _US_MARKET_NAME,
    "Australia",
    "Austria",
    "Belgium",
    "Bermuda",
    "Canada",
    "Cayman Islands",
    "Cyprus",
    "Denmark",
    "Finland",
    "France",
    "Germany",
    "Great Britain",
    "Greece",
    "Guernsey",
    "Hong Kong",
    "Iceland",
    "Ireland",
    "Isle of Man",
    "Israel",
    "Italy",
    "Japan",
    "Jersey",
    "Liechtenstein",
    "Luxembourg",
    "Macau",
    "Malta",
    "Netherlands",
    "New Zealand",
    "Norway",
    "Portugal",
    "Puerto Rico",
    "Singapore",
    "Spain",
    "Sweden",
    "Switzerland",
    "United Kingdom",
]

# MSCI EM core + common broad-EM / frontier names; English labels as on JustETF / feeds.
# Aliases (e.g. UAE, Czechia) are separate strings because matching is exact.
_LIST_OF_EMERGING_MARKETS = [
    "Argentina",
    "Bahrain",
    "Bangladesh",
    "Brazil",
    "Bulgaria",
    "Chech Republic",
    "Chile",
    "China",
    "Colombia",
    "Croatia",
    "Czech Republic",
    "Czechia",
    "Egypt",
    "Estonia",
    "Hungary",
    "India",
    "Indonesia",
    "Kazakhstan",
    "Kenya",
    "Korea",
    "Kuwait",
    "Latvia",
    "Lithuania",
    "Malaysia",
    "Mexico",
    "Morocco",
    "Nigeria",
    "Oman",
    "Pakistan",
    "Peru",
    "Philippines",
    "Poland",
    "Qatar",
    "Romania",
    "Russia",
    "Russian Federation",
    "Saudi Arabia",
    "Serbia",
    "Slovenia",
    "South Africa",
    "South Korea",
    "Sri Lanka",
    "Taiwan",
    "Thailand",
    "Turkey",
    "Türkiye",
    "UAE",
    "Ukraine",
    "United Arab Emirates",
    "Uruguay",
    "Vietnam",
]


class Position(ABC):
    """ISIN-based price lookup: fast quote vs last historical close."""

    def __init__(
        self, isin: str,
        name: str | None = None,
        short_name: str | None = None,
        shares: float | None = None,
        value: float | None = None,
        broker: str | None = None,
        dmem: float | None = None,
        usavn: float | None = None,
        dmem_other: float | None = None,
        last_price: float | None = None,
        cached_countries: dict[str, float] | None = None,
        value_scale: float = 1.0,
    ) -> None:
        self._name = name
        self._short_name = short_name
        self._shares = shares
        self._value = value
        self._value_scale = value_scale
        self._broker = broker
        self._isin = isin
        self._dmem = dmem
        self._dmem_other = dmem_other
        self._usavn = usavn
        self._countries: list[dict[str, float | str]] | None = None
        logger.info("Position: initializing with isin: %s, name: %s, broker: %s, dmem: %s, usavn: %s, dmem_other: %s",
            isin,
            name,
            broker,
            dmem,
            usavn,
            dmem_other,
        )
        logger.info("Position: shares: %s, value: %s", shares, value)
        logger.info("Position: short_name: %s", short_name)
        # Country weights from cache.json are fractions (0–1); internal rows use weight_pct (0–100).
        if cached_countries is not None:
            logger.info("Position: cached countries: %s", cached_countries)
            self._countries = [
                {"name": name, "weight_pct": float(w) * 100.0}
                for name, w in cached_countries.items()
            ]
        # If last_price is provided, it was loaded from cache or supplied by the caller.
        if last_price is not None:
            logger.info("Position: last price: %s", last_price)
            self._last_price = last_price
        # if not, let's try and determine it from the ISIN
        elif self._isin is not None:
            logger.info("Position: fetching last price from ISIN %s", self._isin)
            self._last_price = self._fast_info_price()
            # if the fetch was unsuccessful
            if self._last_price is None:
                logger.error("Position: no fast/quote price for ISIN %s", self._isin)
                raise RuntimeError(f"No fast/quote price for ISIN {self._isin}")
            logger.info("Position: last price: %s", self._last_price)
        # otherwise, no price is cached or determined if only the value of the position is provided
        elif self._value is None:
            logger.error("Position: No last price, neither value nor ISIN was provided")
            raise RuntimeError(
                "No last price for position because neither value nor ISIN was provided"
            )
        if self.countries():
            logger.info("Position: countries: %s", self.countries())
            logger.info("Position: computing DMEM and USAVN")
            self._dmem = self._compute_dev_vs_em_market()
            logger.info("Position: computed DMEM: %s", self._dmem)
            self._usavn = self._compute_us_vs_exus_market()
            logger.info("Position: computed USAVN: %s", self._usavn)

    @property
    def isin(self) -> str:
        return self._isin

    @property
    def value(self) -> float | None:
        base: float | None
        if self._value is not None:
            base = self._value
        elif self._shares is not None and self._last_price is not None:
            base = self._shares * self._last_price
        else:
            base = None
        if base is None:
            return None
        return base * self._value_scale

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
            logger.warning("No historical close for ISIN %s", self._isin)
            raise RuntimeError(f"No historical close for ISIN {self._isin}")
        return float(p)

    def countries(self) -> list[dict[str, float | str]]:
        return self._countries

    def __str__(self) -> str:
        countries_list = self.countries()
        countries_str = ""
        if countries_list:
            countries_str = (
                "Countries: \n" +
                "".join(f"{_row['name']}: {_row['weight_pct']:.2f}%\n" for _row in countries_list)
            )
        dmem_str = f"{self.dmem*100:.2f}%" if self.dmem is not None else "None"
        usavn_str = f"{self.usavn*100:.2f}%" if self.usavn is not None else "None"
        return (
            f"*************** ISIN: {self.isin} ***************\n"
            f"Name: {self._name} \n"
            f"Value: {self.value:.2f} \n"
            f"DMEM: {dmem_str} \n"
            f"USAVN: {usavn_str} \n"
            f"{countries_str}"
        )

    def __repr__(self) -> str:
        return self.__str__()

    def _compute_dev_vs_em_market(self) -> float:
        """Compute developed markets vs. emerging markets allocation."""
        developed_markets = 0
        emerging_markets = 0
        for _row in self.countries():
            if _row["name"] in _LIST_OF_DEVELOPED_MARKETS:
                developed_markets += _row["weight_pct"]
            elif _row["name"] in _LIST_OF_EMERGING_MARKETS:
                emerging_markets += _row["weight_pct"]
            elif self._dmem_other is not None:
                developed_markets += _row["weight_pct"] * self._dmem_other
                emerging_markets += _row["weight_pct"] * (1 - self._dmem_other)
            else:
                developed_markets += _row["weight_pct"] * .5
                emerging_markets += _row["weight_pct"] * .5

        total = developed_markets + emerging_markets
        if total > 0:
            return developed_markets / total
        if self._countries:
            logger.warning(
                "DMEM weights sum to zero for ISIN %s (%d country rows); using 0.0",
                self._isin,
                len(self._countries),
            )
        return 0

    def _compute_us_vs_exus_market(self) -> float:
        """Compute US vs. ex-US allocation within developed markets."""
        us = 0
        non_us = 0
        for _row in self.countries():
            if _row["name"] == _US_MARKET_NAME:
                us += _row["weight_pct"]
            elif _row["name"] in _LIST_OF_DEVELOPED_MARKETS:
                non_us += _row["weight_pct"]
            elif _row["name"] == _OTHER_MARKET_NAME:
                if self._dmem_other is not None:
                    non_us += _row["weight_pct"] * self._dmem_other
                else:
                    non_us += _row["weight_pct"] * .5
        if us + non_us > 0:
            return us / (us + non_us)
        return 0

    @abstractmethod
    def _fast_info_price(self) -> float | None:
        """Current/quick price (e.g. Yahoo fast_info or JustETF latestQuote)."""
        ...

    @abstractmethod
    def _history_last_close(self) -> float | None:
        """Last available daily close from history/chart series."""
        ...
