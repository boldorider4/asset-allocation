"""
Smoke test: OSKAR cockpit manual login + «Aktuelle Gewichtung» fetch.

Requires ``playwright install chromium`` and network. Sign in yourself in the
headed browser when prompted. No credential files are read.

Run from repo root::

    python -m unittest tests.test_oskar_login -v

With pytest (install dev extras: ``pip install -e ".[dev]"``)::

    pytest tests/test_oskar_login.py -v

Optional full-page JSON dumps (after Gewichtung, ``expand-aktien-top`` / ``expand-anleihen-top``,
then ``expand-aktien-opened-aktien-small-cap``-style names for each submenu chevron)::

    OSKAR_DUMP_PAGE_JSON=1 pytest tests/test_oskar_login.py -s -v
"""

from __future__ import annotations

import logging
import unittest

logger = logging.getLogger(__name__)


class TestOskarLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        logging.getLogger(__name__).setLevel(logging.INFO)
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )

    def test_login_and_weighting_tab(self) -> None:
        from position.oskar_position import OskarPosition, fetch_oskar_weighting_etfs

        logger.info("OSKAR login test: start (manual Auth0, headed browser)")

        pos = OskarPosition(
            isin="IE00BF4RFH31",
            name="iShares MSCI World Small Cap UCITS ETF",
            short_name="MSCI World Small Cap",
            shares=1.0,
            value=100.0,
            broker="oskar",
            last_price=1.0,
        )
        self.assertEqual(pos.isin, "IE00BF4RFH31")

        logger.info("OSKAR login test: calling fetch_oskar_weighting_etfs (headed, ~5 min login wait)")
        rows = fetch_oskar_weighting_etfs(
            headless=False,
            timeout_ms=120_000,
            extra_log=logger,
        )
        self.assertIsInstance(rows, list)
        self.assertIsNotNone(rows)
        logger.info("OSKAR login test: done weighting rows=%d", len(rows))
