# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crypto ETC/ETP position: JustETF pricing without geo/sector scrapes.

Every crypto registry entry resolves to this class (whether the issuer
would normally specialize), so a crypto ETC behaves like a bonds-app
JustETF product for quotes, but carries no country table and hardcodes
its sector as a single ``Cryptocurrencies`` wedge.
"""

from __future__ import annotations

import logging

from cli.logger import attach_color_stderr_handler_for_module
from position.justetf_position import JustETFPosition

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

#: The one wedge every crypto position contributes (rows use 0–100).
CRYPTO_SECTOR_LABEL = "Cryptocurrencies"


class CryptoPosition(JustETFPosition):
    """Crypto ETC/ETP tracked on JustETF, but never geo/sector-scraped.

    Split rows are resolved locally (empty countries, one hardcoded
    sector), cached split rows are discarded, and the price/currency
    machinery of :class:`JustETFPosition` is inherited untouched. Only
    ISIN-tracked products are supported: rows without an ISIN never
    reach the factory.
    """

    def __init__(self, isin: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        # Whatever the cache holds (possibly scraped under an older
        # equity assignment) must not leak into a crypto split.
        kwargs["cached_countries"] = {}
        kwargs["cached_sectors"] = None
        super().__init__(isin, **kwargs)

    def _fetch_countries_for_geosplit(
        self,
    ) -> list[dict[str, float | str]] | None:
        """Crypto products carry no country table; never hit the vendor."""
        logger.info(
            "CryptoPosition: geosplit disabled for %s (no regional data)",
            self._isin,
        )
        return []

    def _fetch_sectors_for_sectorsplit(
        self,
    ) -> list[dict[str, float | str]] | None:
        """Hardcode the sector: one full ``Cryptocurrencies`` wedge."""
        logger.info(
            "CryptoPosition: sector %r hardcoded for %s",
            CRYPTO_SECTOR_LABEL,
            self._isin,
        )
        return [{"name": CRYPTO_SECTOR_LABEL, "weight_pct": 100.0}]

    def countries(self) -> list[dict[str, float | str]]:
        """Empty country table; never lazy-fetches from JustETF."""
        return self._countries if self._countries is not None else []

    def sectors(self) -> list[dict[str, float | str]] | None:
        """Cached/hardcoded sector rows or nothing; never lazy-fetches.

        The JustETF parent lazily hits the vendor when called with no
        loaded rows; crypto has nothing to load (no split data of any
        kind), so callers see an honest ``None`` instead of an HTTP trip.
        """
        return self._sectors
