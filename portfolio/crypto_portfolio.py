# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crypto ETC/ETP portfolio for chart composition.

A :class:`NonRegionalPortfolio` whose positions aggregate into a single
hardcoded ``Cryptocurrencies`` sector wedge (covered: ISIN-tracked
CryptoPosition rows and manual, ISIN-less entries alike), so the sector
chart never depends on vendor data that crypto products don't carry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cli.logger import attach_color_stderr_handler_for_module
from position.crypto_position import CRYPTO_SECTOR_LABEL
from portfolio.non_regional_portfolio import NonRegionalPortfolio

if TYPE_CHECKING:
    from cli.context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


class CryptoPortfolio(NonRegionalPortfolio):
    """Non-regional portfolio whose sector is one ``Cryptocurrencies`` wedge."""

    def _calculate_sectors(self) -> dict[str, float]:
        if self._value <= 0:
            return {}
        logger.info(
            "CryptoPortfolio %r: sector hardcoded %r", self._name,
            CRYPTO_SECTOR_LABEL,
        )
        return {CRYPTO_SECTOR_LABEL: 1.0}
