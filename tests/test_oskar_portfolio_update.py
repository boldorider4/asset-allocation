"""Unit tests for ``update_oskar_etfs_in_portfolio`` (no Playwright)."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from oskar import OskarEtf, _OSKAR_TAGESGELD_FETCH_KEY, update_oskar_etfs_in_portfolio
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
    def test_removes_stale_oskar_position(self, mock_fetch) -> None:
        global_portfolio["equity_portfolio"].append(
            {
                "name": "Gone OSKAR ETF",
                "ISIN": "IE000STALE00",
                "shares": 5,
                "value": 500.0,
                "broker": "oskar",
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
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
        isins = [p["ISIN"] for p in global_portfolio["equity_portfolio"]]
        self.assertEqual(isins, ["IE000EXISTING"])

    @patch("oskar.fetch_oskar_etfs")
    def test_does_not_remove_non_oskar_position_with_missing_isin(self, mock_fetch) -> None:
        global_portfolio["equity_portfolio"].append(
            {
                "name": "Scalable ETF",
                "ISIN": "IE000STALE00",
                "shares": 5,
                "value": 500.0,
                "broker": "scalable",
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
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
        isins = [p["ISIN"] for p in global_portfolio["equity_portfolio"]]
        self.assertIn("IE000STALE00", isins)
        self.assertIn("IE000EXISTING", isins)

    @patch("oskar.fetch_oskar_etfs")
    def test_leaves_portfolio_unchanged_when_fetch_is_empty(self, mock_fetch) -> None:
        mock_fetch.return_value = {}
        with self.assertLogs("oskar", level="WARNING") as logs:
            update_oskar_etfs_in_portfolio()
        self.assertEqual(len(global_portfolio["equity_portfolio"]), 1)
        self.assertEqual(global_portfolio["equity_portfolio"][0]["ISIN"], "IE000EXISTING")
        self.assertTrue(
            any("no OSKAR ETFs fetched" in msg for msg in logs.output),
            logs.output,
        )

    @patch("oskar.fetch_oskar_etfs")
    def test_does_not_remove_oskar_tagesgeld_without_isin(self, mock_fetch) -> None:
        global_portfolio["cash_portfolio"] = [
            {
                "name": "Tagesgeld",
                "value": 500.0,
                "broker": "oskar",
                "ISIN": None,
                "dmem": None,
                "usavn": None,
            }
        ]
        global_portfolio["equity_portfolio"].append(
            {
                "name": "Gone OSKAR ETF",
                "ISIN": "IE000STALE00",
                "shares": 5,
                "value": 500.0,
                "broker": "oskar",
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
        mock_fetch.return_value = {
            "IE000EXISTING": OskarEtf(
                isin="IE000EXISTING",
                name="Existing OSKAR ETF",
                weight_pct=50.0,
                value_eur=1234.5,
                raw_text="",
            ),
            "LU0999999999": OskarEtf(
                isin="LU0999999999",
                name="Tagesgeld",
                weight_pct=5.0,
                value_eur=777.0,
                raw_text="",
                category="Tagesgeld",
            ),
        }
        update_oskar_etfs_in_portfolio()
        tagesgeld_without_isin = [
            p
            for p in global_portfolio["cash_portfolio"]
            if p["name"] == "Tagesgeld" and p.get("ISIN") is None
        ]
        self.assertEqual(len(tagesgeld_without_isin), 1)
        self.assertEqual(tagesgeld_without_isin[0]["value"], 777.0)
        self.assertEqual(len(global_portfolio["cash_portfolio"]), 1)
        equity_isins = [p["ISIN"] for p in global_portfolio["equity_portfolio"]]
        self.assertEqual(equity_isins, ["IE000EXISTING"])

    @patch("oskar.fetch_oskar_etfs")
    def test_updates_oskar_tagesgeld_when_fetched(self, mock_fetch) -> None:
        global_portfolio["cash_portfolio"] = [
            {
                "name": "Tagesgeld",
                "value": 500.0,
                "broker": "oskar",
                "ISIN": None,
                "dmem": None,
                "usavn": None,
            }
        ]
        mock_fetch.return_value = {
            _OSKAR_TAGESGELD_FETCH_KEY: OskarEtf(
                isin=_OSKAR_TAGESGELD_FETCH_KEY,
                name="Tagesgeld",
                weight_pct=5.0,
                value_eur=777.0,
                raw_text="Tagesgeld 5,0 % 777,00 €",
                category="Tagesgeld",
            ),
        }
        update_oskar_etfs_in_portfolio()
        tagesgeld = next(
            p
            for p in global_portfolio["cash_portfolio"]
            if p["name"] == "Tagesgeld" and p.get("ISIN") is None
        )
        self.assertEqual(tagesgeld["value"], 777.0)
        self.assertIsNone(tagesgeld["shares"])
        self.assertEqual(len(global_portfolio["cash_portfolio"]), 1)

    @patch("oskar.fetch_oskar_etfs")
    def test_adds_oskar_tagesgeld_when_missing_from_portfolio(self, mock_fetch) -> None:
        global_portfolio["cash_portfolio"] = []
        mock_fetch.return_value = {
            _OSKAR_TAGESGELD_FETCH_KEY: OskarEtf(
                isin=_OSKAR_TAGESGELD_FETCH_KEY,
                name="Tagesgeld",
                weight_pct=5.0,
                value_eur=777.0,
                raw_text="",
                category="Tagesgeld",
            )
        }
        update_oskar_etfs_in_portfolio()
        self.assertEqual(len(global_portfolio["cash_portfolio"]), 1)
        pos = global_portfolio["cash_portfolio"][0]
        self.assertEqual(pos["name"], "Tagesgeld")
        self.assertIsNone(pos["ISIN"])
        self.assertEqual(pos["value"], 777.0)
        self.assertEqual(pos["broker"], "oskar")

    @patch("oskar.fetch_oskar_etfs")
    def test_removes_oskar_position_without_isin_unless_tagesgeld(self, mock_fetch) -> None:
        global_portfolio["equity_portfolio"].append(
            {
                "name": "Manual OSKAR Entry",
                "value": 250.0,
                "broker": "oskar",
                "ISIN": None,
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0,
            }
        )
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
        names = [p["name"] for p in global_portfolio["equity_portfolio"]]
        self.assertNotIn("Manual OSKAR Entry", names)
        self.assertIn("Existing OSKAR ETF", names)

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
