"""Unit tests for ``update_scalable_etfs_in_portfolio`` (no ``sc`` CLI)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import (
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
    FIXED_MATURITY_BOND_PORTFOLIO,
)
from scrape.scalable import (
    ScalableHolding,
    _TAGESGELD_FETCH_KEY,
    update_scalable_etfs_in_portfolio,
)
from utils import (
    get_fetch_prices,
    portfolio as global_portfolio,
    set_fetch_prices,
    write_portfolio_to_file,
)


class TestUpdateScalableEtfsInPortfolio(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = copy.deepcopy(dict(global_portfolio))
        self._prices = get_fetch_prices()
        global_portfolio.clear()
        global_portfolio.update(
            {
                EQUITY_PORTFOLIO: [
                    {
                        "name": "Existing Scalable ETF",
                        "ISIN": "IE0006WW1TQ4",
                        "shares": 1,
                        "value": 10.0,
                        "broker": "scalable",
                        "dmem": 1,
                        "dmem_other": 1,
                        "usavn": 0,
                    }
                ],
                COMMODITY_PORTFOLIO: [],
                FIXED_MATURITY_BOND_PORTFOLIO: [],
                CASH_PORTFOLIO: [
                    {
                        "name": "Tagesgeld",
                        "value": 1.0,
                        "broker": "scalable",
                        "ISIN": None,
                    }
                ],
            }
        )

    def tearDown(self) -> None:
        set_fetch_prices(self._prices)
        global_portfolio.clear()
        global_portfolio.update(copy.deepcopy(self._saved))

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_updates_shares_value_and_price(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers MSCI World ex USA (Acc)",
                shares=4,
                value=140.0,
                price=40.315,
            ),
            _TAGESGELD_FETCH_KEY: ScalableHolding(
                isin=None,
                name="Tagesgeld",
                shares=None,
                value=40.32,
                price=None,
                is_tagesgeld=True,
            ),
        }
        update_scalable_etfs_in_portfolio()
        pos = global_portfolio[EQUITY_PORTFOLIO][0]
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["value"], 140.0)
        self.assertNotIn("price", pos)
        self.assertEqual(global_portfolio[CASH_PORTFOLIO][0]["value"], 40.32)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_writes_assets_file_shares_and_value(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        update_scalable_etfs_in_portfolio()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "assets.json"
            write_portfolio_to_file(path)
            written = json.loads(path.read_text(encoding="utf-8"))
        pos = written[EQUITY_PORTFOLIO][0]
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["value"], 140.0)
        self.assertNotIn("price", pos)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_adds_mapped_buckets(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "DE000EWG2LD7": ScalableHolding(
                isin="DE000EWG2LD7",
                name="EUWAX Gold II",
                shares=1,
                value=270.0,
                price=129.6555,
            ),
            "LU2233156582": ScalableHolding(
                isin="LU2233156582",
                name="Amundi Prime Euro Gov",
                shares=10,
                value=100.0,
                price=10.0,
            ),
        }
        update_scalable_etfs_in_portfolio()
        self.assertEqual(global_portfolio[COMMODITY_PORTFOLIO][0]["ISIN"], "DE000EWG2LD7")
        self.assertEqual(
            global_portfolio[FIXED_MATURITY_BOND_PORTFOLIO][0]["ISIN"],
            "LU2233156582",
        )
        self.assertEqual(len(global_portfolio[EQUITY_PORTFOLIO]), 0)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_unknown_isin_falls_back_to_equity(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "XX000UNKNOWN1": ScalableHolding(
                isin="XX000UNKNOWN1",
                name="Unknown",
                shares=2,
                value=20.0,
                price=10.0,
            ),
        }
        with self.assertLogs("utils", level="WARNING"):
            update_scalable_etfs_in_portfolio()
        self.assertEqual(global_portfolio[EQUITY_PORTFOLIO][0]["ISIN"], "XX000UNKNOWN1")

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_removes_stale_and_keeps_non_scalable(self, mock_fetch) -> None:
        global_portfolio[EQUITY_PORTFOLIO].append(
            {
                "name": "Oskar leftover",
                "ISIN": "IE00OSTALE01",
                "shares": 1,
                "value": 1.0,
                "broker": "oskar",
            }
        )
        mock_fetch.return_value = {
            "IE000BI8OT95": ScalableHolding(
                isin="IE000BI8OT95",
                name="Amundi",
                shares=3,
                value=30.0,
                price=10.0,
            ),
        }
        update_scalable_etfs_in_portfolio()
        isins = [p["ISIN"] for p in global_portfolio[EQUITY_PORTFOLIO]]
        self.assertIn("IE000BI8OT95", isins)
        self.assertIn("IE00OSTALE01", isins)
        self.assertNotIn("IE0006WW1TQ4", isins)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_removes_tagesgeld_when_overnight_absent(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        update_scalable_etfs_in_portfolio()
        self.assertEqual(global_portfolio[CASH_PORTFOLIO], [])

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_empty_fetch_leaves_portfolio_unchanged(self, mock_fetch) -> None:
        mock_fetch.return_value = {}
        with self.assertLogs("scrape.scalable", level="WARNING"):
            update_scalable_etfs_in_portfolio()
        self.assertEqual(len(global_portfolio[EQUITY_PORTFOLIO]), 1)
        self.assertEqual(global_portfolio[CASH_PORTFOLIO][0]["value"], 1.0)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_fetch_prices_writes_broker_quote_to_cache(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        set_fetch_prices(True)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            with patch("utils.CACHE_FILENAME", str(cache_path)):
                update_scalable_etfs_in_portfolio()
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE0006WW1TQ4"]["price"], 40.315)
        self.assertNotIn("price", global_portfolio[EQUITY_PORTFOLIO][0])

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_without_fetch_prices_does_not_write_cache(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        set_fetch_prices(False)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            with patch("utils.CACHE_FILENAME", str(cache_path)):
                update_scalable_etfs_in_portfolio()
            self.assertEqual(cache_path.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
