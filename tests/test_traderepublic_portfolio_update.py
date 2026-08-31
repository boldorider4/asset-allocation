"""Unit tests for ``update_traderepublic_etfs_in_portfolio`` (no pytr login)."""

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
from traderepublic import (
    TradeRepublicHolding,
    _CASH_FETCH_KEY,
    update_traderepublic_etfs_in_portfolio,
)
from utils import portfolio as global_portfolio
from utils import write_portfolio_to_file


class TestUpdateTradeRepublicEtfsInPortfolio(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = copy.deepcopy(dict(global_portfolio))
        global_portfolio.clear()
        global_portfolio.update(
            {
                EQUITY_PORTFOLIO: [
                    {
                        "name": "Existing TR ETF",
                        "ISIN": "IE0006WW1TQ4",
                        "shares": 1,
                        "value": 10.0,
                        "broker": "traderepublic",
                        "dmem": 1,
                        "dmem_other": 1,
                        "usavn": 0,
                    }
                ],
                COMMODITY_PORTFOLIO: [],
                FIXED_MATURITY_BOND_PORTFOLIO: [],
                CASH_PORTFOLIO: [
                    {
                        "name": "Cash",
                        "value": 1.0,
                        "broker": "traderepublic",
                        "ISIN": None,
                    }
                ],
            }
        )

    def tearDown(self) -> None:
        global_portfolio.clear()
        global_portfolio.update(copy.deepcopy(self._saved))

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_updates_shares_value_and_price(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": TradeRepublicHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers MSCI World ex USA (Acc)",
                shares=4,
                value=140.0,
                price=40.315,
            ),
            _CASH_FETCH_KEY: TradeRepublicHolding(
                isin=None,
                name="Cash",
                shares=None,
                value=40.32,
                price=None,
                is_cash=True,
            ),
        }
        update_traderepublic_etfs_in_portfolio()
        pos = global_portfolio[EQUITY_PORTFOLIO][0]
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["value"], 140.0)
        self.assertEqual(pos["price"], 40.315)
        self.assertEqual(global_portfolio[CASH_PORTFOLIO][0]["value"], 40.32)

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_writes_assets_file_shares_and_value(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": TradeRepublicHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        update_traderepublic_etfs_in_portfolio()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "assets.json"
            write_portfolio_to_file(path)
            written = json.loads(path.read_text(encoding="utf-8"))
        pos = written[EQUITY_PORTFOLIO][0]
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["value"], 140.0)
        self.assertEqual(pos["price"], 40.315)

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_adds_mapped_buckets(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "DE000EWG2LD7": TradeRepublicHolding(
                isin="DE000EWG2LD7",
                name="EUWAX Gold II",
                shares=1,
                value=270.0,
                price=129.6555,
            ),
            "LU2233156582": TradeRepublicHolding(
                isin="LU2233156582",
                name="Amundi Prime Euro Gov",
                shares=10,
                value=100.0,
                price=10.0,
            ),
        }
        update_traderepublic_etfs_in_portfolio()
        self.assertEqual(global_portfolio[COMMODITY_PORTFOLIO][0]["ISIN"], "DE000EWG2LD7")
        self.assertEqual(
            global_portfolio[FIXED_MATURITY_BOND_PORTFOLIO][0]["ISIN"],
            "LU2233156582",
        )
        self.assertEqual(len(global_portfolio[EQUITY_PORTFOLIO]), 0)

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_unknown_isin_falls_back_to_equity(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "XX000UNKNOWN1": TradeRepublicHolding(
                isin="XX000UNKNOWN1",
                name="Unknown",
                shares=2,
                value=20.0,
                price=10.0,
            ),
        }
        with self.assertLogs("utils", level="WARNING"):
            update_traderepublic_etfs_in_portfolio()
        self.assertEqual(global_portfolio[EQUITY_PORTFOLIO][0]["ISIN"], "XX000UNKNOWN1")

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_removes_stale_and_keeps_non_traderepublic(self, mock_fetch) -> None:
        global_portfolio[EQUITY_PORTFOLIO].append(
            {
                "name": "Scalable leftover",
                "ISIN": "IE00OSTALE01",
                "shares": 1,
                "value": 1.0,
                "broker": "scalable",
            }
        )
        mock_fetch.return_value = {
            "IE000BI8OT95": TradeRepublicHolding(
                isin="IE000BI8OT95",
                name="Amundi",
                shares=3,
                value=30.0,
                price=10.0,
            ),
        }
        update_traderepublic_etfs_in_portfolio()
        isins = [p["ISIN"] for p in global_portfolio[EQUITY_PORTFOLIO]]
        self.assertIn("IE000BI8OT95", isins)
        self.assertIn("IE00OSTALE01", isins)
        self.assertNotIn("IE0006WW1TQ4", isins)

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_removes_cash_when_absent(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": TradeRepublicHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }
        update_traderepublic_etfs_in_portfolio()
        self.assertEqual(global_portfolio[CASH_PORTFOLIO], [])

    @patch("traderepublic.fetch_traderepublic_etfs")
    def test_empty_fetch_leaves_portfolio_unchanged(self, mock_fetch) -> None:
        mock_fetch.return_value = {}
        with self.assertLogs("traderepublic", level="WARNING"):
            update_traderepublic_etfs_in_portfolio()
        self.assertEqual(len(global_portfolio[EQUITY_PORTFOLIO]), 1)
        self.assertEqual(global_portfolio[CASH_PORTFOLIO][0]["value"], 1.0)


if __name__ == "__main__":
    unittest.main()
