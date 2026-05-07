"""
Smoke test: OSKAR cockpit login + «Aktuelle Gewichtung» fetch.

Requires ``oskar.cred.ini`` with ``email`` (or ``username``), a non-empty
``password`` **or** ``OSKAR_PASSWORD`` in the environment (so the test never
blocks on ``getpass``), ``playwright install chromium``, and network.

Run from repo root::

    python -m unittest tests.test_oskar_login -v

With pytest (install dev extras: ``pip install -e ".[dev]"``), live INFO logs
from ``position.oskar_position`` are shown automatically (see ``conftest.py`` and
``[tool.pytest.ini_options]`` in ``pyproject.toml``)::

    pytest tests/test_oskar_login.py -v
"""

from __future__ import annotations

import configparser
import logging
import os
import unittest
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CRED_PATH = _REPO_ROOT / "oskar.cred.ini"


def _credentials_ready() -> bool:
    if not _CRED_PATH.is_file():
        return False
    cfg = configparser.ConfigParser()
    cfg.read(_CRED_PATH, encoding="utf-8")
    if "oskar" not in cfg:
        return False
    sec = cfg["oskar"]
    email = (sec.get("email") or sec.get("username") or "").strip()
    if not email:
        return False
    pw = (sec.get("password") or "").strip() or os.environ.get("OSKAR_PASSWORD", "").strip()
    return bool(pw)


_SKIP = (
    None
    if _credentials_ready()
    else (
        "needs oskar.cred.ini with email + password, or email in ini and OSKAR_PASSWORD "
        "(non-interactive; avoids getpass hang)"
    )
)


@unittest.skipIf(_SKIP is not None, _SKIP or "")
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
        from position.oskar_position import OskarCredentials, OskarPosition, fetch_oskar_weighting_etfs

        logger.info("OSKAR login test: start cred_path=%s", _CRED_PATH)

        cfg = configparser.ConfigParser()
        cfg.read(_CRED_PATH, encoding="utf-8")
        sec = cfg["oskar"]
        email = (sec.get("email") or sec.get("username") or "").strip()
        password = (sec.get("password") or "").strip() or os.environ.get("OSKAR_PASSWORD", "").strip()
        pw_source = "ini" if (sec.get("password") or "").strip() else "OSKAR_PASSWORD"
        logger.info(
            "OSKAR login test: credentials loaded (email len=%d, password from %s)",
            len(email),
            pw_source,
        )
        creds = OskarCredentials(email=email, password=password)

        # Example row like the cockpit UI; last_price skips JustETF HTTP on init.
        logger.info("OSKAR login test: instantiating OskarPosition sample (ISIN IE00BF4RFH31)")
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
        logger.info("OSKAR login test: calling fetch_oskar_weighting_etfs(headless=True)")

        rows = fetch_oskar_weighting_etfs(creds, headless=True, timeout_ms=120_000)
        self.assertIsInstance(rows, list)
        self.assertIsNotNone(rows)
        logger.info("OSKAR login test: done weighting rows=%d", len(rows))
