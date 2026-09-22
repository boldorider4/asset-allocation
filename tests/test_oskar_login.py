# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Smoke test: OSKAR cockpit headless CLI login + «Aktuelle Gewichtung» fetch.

Requires ``playwright install chromium`` and network. The Auth0 email is read
with ``input()`` and the password with ``getpass`` (never echoed); type them
in the terminal when prompted. No credential files are read.

Run from repo root in a real terminal (stdin must be a TTY)::

    python -m unittest tests.test_oskar_login -v

With pytest (install dev extras: ``pip install -e ".[dev]"``). If Playwright
browsers are installed under a Cursor sandbox path, force the default cache.
``-s`` is required so the credential prompts can read stdin::

    PLAYWRIGHT_BROWSERS_PATH=0 pytest tests/test_oskar_login.py -s -v --log-cli-level=DEBUG

Each test signs in once, so pick a single one to avoid logging in twice::

    PLAYWRIGHT_BROWSERS_PATH=0 pytest tests/test_oskar_login.py -s -v \
        -k test_login_then_headless_handover

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

    def test_login_and_oskar_etfs(self) -> None:
        from scrape.oskar import fetch_oskar_etfs

        logger.info("OSKAR login test: start (headless CLI Auth0, ~5 min login wait)")
        import sys

        if not sys.stdin.isatty():
            self.skipTest("headless CLI login needs an interactive terminal")

        logger.info("OSKAR login test: calling fetch_oskar_etfs (headless)")
        rows = fetch_oskar_etfs(timeout_ms=120_000)
        self.assertIsInstance(rows, dict)
        self.assertGreater(len(rows), 0)
        logger.info("OSKAR login test: done rows=%d", len(rows))

    def test_login_then_scrape_stays_in_same_browser(self) -> None:
        """
        The whole run — CLI login plus scrape — happens in a single headless
        browser; there is no headed window and no session handover.
        """
        from scrape.oskar import fetch_oskar_etfs

        logger.info("OSKAR same-browser test: start (headless CLI Auth0)")
        import sys

        if not sys.stdin.isatty():
            self.skipTest("headless CLI login needs an interactive terminal")

        rows = fetch_oskar_etfs(timeout_ms=120_000)
        self.assertIsInstance(rows, dict)
        self.assertGreater(len(rows), 0)
        logger.info("OSKAR same-browser test: done rows=%d", len(rows))
