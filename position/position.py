"""
Position pricing: abstract base plus Yahoo Finance and JustETF implementations.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from logger import attach_color_stderr_handler_for_module
from scrape.oskar import _OSKAR
from utils import get_fetch_geosplit, get_fetch_prices

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
        cached_countries: dict[str, float] | None = None,
        value_scale: float = 1.0,
        price: float | None = None,
        prefer_scrape_value: bool = False,
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
        self._prefer_scrape_value = prefer_scrape_value
        self._countries: list[dict[str, float | str]] | None = None
        self._price: float | None = None
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

        cached_rows = self._cached_countries_to_rows(cached_countries)
        if get_fetch_geosplit():
            self._countries = self._fetch_countries_for_geosplit()
        elif cached_rows is not None:
            if self._isin:
                logger.warning("Position: cached countries for ISIN %s: %s", self._isin, cached_countries)
            else:
                logger.warning("Position: cached countries for asset %s: %s", self._name, cached_countries)
            self._countries = cached_rows
        else:
            logger.warning(
                "Position: fetch-geosplit disabled and no cached countries for %s",
                self._isin or self._name,
            )
        logger.info("Position: countries: %s", self._countries)

        # countries are set either from cache.json or from ISIN
        if self._countries is not None:
            logger.info("Position: countries are set, using them to compute DMEM and USAVN")
        elif self._isin:
            logger.warning("Position: no countries found for ISIN %s, using dmem and usavn from asset file", self._isin)
        else:
            logger.warning("Position: no countries found for asset %s, using dmem and usavn from asset file", self._name)

        self._dmem = self._compute_dev_vs_em_market()
        logger.info("Position: DMEM: %s", self._dmem)
        self._usavn = self._compute_us_vs_exus_market()
        logger.info("Position: USAVN: %s", self._usavn)

        if get_fetch_prices():
            self._price = self._fetch_fast_info_price(price)
        elif price is not None:
            logger.info("Position: using supplied price: %s", price)
            self._price = price
        elif self._isin and self._broker is not _OSKAR:
            logger.info(
                "Position: no supplied price; fetching from ISIN %s (fetch-prices disabled)",
                self._isin,
            )
            self._price = self._fetch_fast_info_price(None)
        elif self._value is None:
            logger.error("Position: No price, neither value nor ISIN was provided")
            raise RuntimeError(
                "No price for position because neither value nor ISIN was provided"
            )

    @property
    def isin(self) -> str:
        return self._isin

    @property
    def value(self) -> float | None:
        base: float | None
        share_and_price_available = self._shares is not None and self._price is not None
        if self._prefer_scrape_value and self._value is not None:
            logger.info("Position: using cached value from asset file because preferred: %s", self._value)
            base = self._value
        elif share_and_price_available:
            logger.info("Position: using shares and price to compute value because cached \
                value not preferred or no cached value is available and shares and price are available")
            base = self._shares * self._price
        elif self._value is not None:
            logger.info("Position: using cached value from asset file because no price is fetched")
            base = self._value
        if base is None:
            logger.warning("Position: no value to compute")
            return None
        logger.info("Position: computed value: %s", base * self._value_scale)
        return base * self._value_scale

    @property
    def dmem(self) -> float | None:
        return self._dmem

    @property
    def usavn(self) -> float | None:
        return self._usavn

    @property
    def price(self) -> float | None:
        return self._price

    def price_history(self) -> float:
        p = self._history_last_close()
        if p is None:
            logger.warning("No historical close for ISIN %s", self._isin)
            raise RuntimeError(f"No historical close for ISIN {self._isin}")
        return float(p)

    def countries(self) -> list[dict[str, float | str]]:
        return self._countries

    def __str__(self) -> str:
        countries_list = self._countries
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

    @staticmethod
    def _cached_countries_to_rows(
        cached_countries: dict[str, float] | None,
    ) -> list[dict[str, float | str]] | None:
        # Country weights from cache.json are fractions (0–1); internal rows use weight_pct (0–100).
        if cached_countries is None:
            return None
        return [
            {"name": name, "weight_pct": float(w) * 100.0}
            for name, w in cached_countries.items()
        ]

    def _fetch_countries_for_geosplit(
        self,
    ) -> list[dict[str, float | str]] | None:
        if not self._isin:
            logger.warning(
                "Position: fetch-geosplit requested but no ISIN for asset %s; "
                "skipping country lookup",
                self._name,
            )
            return None
        logger.info("Position: fetch-geosplit enabled, fetching countries from ISIN %s", self._isin)
        try:
            return self.countries()
        except NotImplementedError:
            logger.warning("Position: could not fetch countries for ISIN %s", self._isin)
            return None

    def _fetch_fast_info_price(self, supplied_price: float | None) -> float | None:
        if not self._isin:
            logger.warning(
                "Position: fetch-prices requested but no ISIN for asset %s; "
                "skipping quote lookup",
                self._name,
            )
            return supplied_price
        if self._broker is _OSKAR:
            logger.warning(
                "Position: fetch-prices requested for OSKAR position %s; not fetching quote",
                self._isin,
            )
            return supplied_price
        logger.info("Position: fetching price from ISIN %s", self._isin)
        try:
            fetched = self._fast_info_price()
        except NotImplementedError:
            logger.warning(
                "Position: quote lookup not implemented for ISIN %s; using supplied price %s",
                self._isin,
                supplied_price,
            )
            return supplied_price
        if fetched is None:
            logger.warning(
                "Position: no price for ISIN %s; continuing without a fetched quote",
                self._isin,
            )
            return None
        logger.info("Position: price: %s", fetched)
        return fetched

    def _compute_dev_vs_em_market(self) -> float:
        """Compute developed markets vs. emerging markets allocation."""
        # default to what is set in dmem property
        developed_markets = self._dmem if self._dmem is not None else 0
        emerging_markets = 1 - self._dmem if self._dmem is not None else 1
        # if countries are set, use them to compute dmem
        for _row in self._countries or []:
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
        # default to what is set in usavn property
        us = self._usavn if self._usavn is not None else 0
        non_us = 1 - self._usavn if self._usavn is not None else 1
        # if countries are set, use them to compute usavn
        for _row in self._countries or []:
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
