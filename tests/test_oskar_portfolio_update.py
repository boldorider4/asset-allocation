"""Unit tests for ``update_oskar_etfs_in_portfolio`` (no Playwright)."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from oskar import OskarEtf, update_oskar_etfs_in_portfolio
from utils import portfolio as global_portfolio


class TestUpdateOskarEtfsInPortfolio(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = copy.deepcopy(dict(global_portfolio))
        global_portfolio.clear()
        global_portfolio.update(
            {
                "equity_portfolio": [
                    {
                        "name": "Existing OSKAR ETF",
                        "ISIN": "IE000EXISTING",
                        "shares": 10,
                        "value": 100.0,
                        "broker": "oskar",
                        "dmem": 1,
                        "dmem_other": 1,
                        "usavn": 0.5,
                    }
                ],
                "bond_portfolio": [],
            }
        )

    def tearDown(self) -> None:
        global_portfolio.clear()
        global_portfolio.update(copy.deepcopy(self._saved))

    @patch("oskar.fetch_oskar_etfs")
    def test_updates_existing_oskar_position(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE000EXISTING": OskarEtf(
                isin="IE000EXISTING",
                name="Existing OSKAR ETF",
                weight_pct=50.0,
                value_eur=1234.5,
                raw_text="",
            )
        }
        update_oskar_etfs_in_portfolio()
        pos = global_portfolio["equity_portfolio"][0]
        self.assertEqual(pos["value"], 1234.5)
        self.assertIsNone(pos["shares"])
        self.assertEqual(len(global_portfolio["equity_portfolio"]), 1)

    @patch("oskar.fetch_oskar_etfs")
    def test_adds_missing_oskar_position_to_bucket(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "LU0123456789": OskarEtf(
                isin="LU0123456789",
                name="New Bond ETF",
                weight_pct=10.0,
                value_eur=999.0,
                raw_text="",
                category="Anleihen",
                subcategory="Anleihen Global",
            )
        }
        update_oskar_etfs_in_portfolio()
        self.assertEqual(len(global_portfolio["bond_portfolio"]), 1)
        pos = global_portfolio["bond_portfolio"][0]
        self.assertEqual(pos["ISIN"], "LU0123456789")
        self.assertEqual(pos["value"], 999.0)
        self.assertIsNone(pos["shares"])
        self.assertEqual(pos["broker"], "oskar")
        self.assertEqual(pos["name"], "New Bond ETF")


if __name__ == "__main__":
    unittest.main()
