"""
Smoke test: OSKAR cockpit manual login + «Aktuelle Gewichtung» fetch.

Requires ``playwright install chromium`` and network. Sign in yourself in the
headed browser when prompted. No credential files are read.

Run from repo root::

    python -m unittest tests.test_oskar_login -v

With pytest (install dev extras: ``pip install -e ".[dev]"``). If Playwright
browsers are installed under a Cursor sandbox path, force the default cache::

    PLAYWRIGHT_BROWSERS_PATH=0 pytest tests/test_oskar_login.py -s -v --log-cli-level=DEBUG

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
        from oskar import fetch_oskar_weighting_etfs

        logger.info("OSKAR login test: start (manual Auth0, headed browser)")

        logger.info("OSKAR login test: calling fetch_oskar_weighting_etfs (headed, ~5 min login wait)")
        rows = fetch_oskar_weighting_etfs(
            headless=False,
            timeout_ms=120_000,
        )
        self.assertIsInstance(rows, list)
        self.assertIsNotNone(rows)
        logger.info("OSKAR login test: done weighting rows=%d", len(rows))
