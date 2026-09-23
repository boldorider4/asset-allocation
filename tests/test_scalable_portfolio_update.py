# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``update_scalable_etfs_in_portfolio`` (no ``sc`` CLI)."""

from __future__ import annotations

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
    _CASH_FETCH_KEY,
    _TAGESGELD_FETCH_KEY,
    update_scalable_etfs_in_portfolio,
)
from context import AppConfig, RuntimeContext
from utils import write_portfolio


class TestUpdateScalableEtfsInPortfolio(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True
        self.ctx.portfolio.update(
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
        update_scalable_etfs_in_portfolio(self.ctx)
        pos = self.ctx.portfolio[EQUITY_PORTFOLIO][0]
        self.assertEqual(pos["shares"], 4)
        self.assertEqual(pos["value"], 140.0)
        self.assertNotIn("price", pos)
        self.assertEqual(self.ctx.portfolio[CASH_PORTFOLIO][0]["value"], 40.32)

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
        update_scalable_etfs_in_portfolio(self.ctx)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "assets.json"
            write_portfolio(path, self.ctx.portfolio)
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
        update_scalable_etfs_in_portfolio(self.ctx)
        self.assertEqual(self.ctx.portfolio[COMMODITY_PORTFOLIO][0]["ISIN"], "DE000EWG2LD7")
        self.assertEqual(
            self.ctx.portfolio[FIXED_MATURITY_BOND_PORTFOLIO][0]["ISIN"],
            "LU2233156582",
        )
        self.assertEqual(len(self.ctx.portfolio[EQUITY_PORTFOLIO]), 0)

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
            update_scalable_etfs_in_portfolio(self.ctx)
        self.assertEqual(self.ctx.portfolio[EQUITY_PORTFOLIO][0]["ISIN"], "XX000UNKNOWN1")

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_removes_stale_and_keeps_non_scalable(self, mock_fetch) -> None:
        self.ctx.portfolio[EQUITY_PORTFOLIO].append(
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
        update_scalable_etfs_in_portfolio(self.ctx)
        isins = [p["ISIN"] for p in self.ctx.portfolio[EQUITY_PORTFOLIO]]
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
        update_scalable_etfs_in_portfolio(self.ctx)
        self.assertEqual(self.ctx.portfolio[CASH_PORTFOLIO], [])

    def _seed_split_cache(self) -> None:
        self.ctx.cache["IE0006WW1TQ4"] = {
            "price": 40.315,
            "countries": {"United States": 0.9},
            "sectors": {"Technology": 0.5},
        }

    def _matched_holding(self) -> dict:
        return {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                price=40.315,
            ),
        }

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_fresh_splits_survive_update_when_flags_on(self, mock_fetch) -> None:
        # The reported bug: --fetch-geosplit --fetch-sectorsplit stages
        # fresh splits, then the update wiped them from the store.
        self.ctx.config.fetch_geosplit = True
        self.ctx.config.fetch_sectorsplit = True
        self._seed_split_cache()
        mock_fetch.return_value = self._matched_holding()
        update_scalable_etfs_in_portfolio(self.ctx)
        row = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(row["countries"], {"United States": 0.9})
        self.assertEqual(row["sectors"], {"Technology": 0.5})

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_stale_splits_cleared_when_flags_off(self, mock_fetch) -> None:
        # Legacy path: nothing freshly scraped, so stale splits are
        # cleared for refetch on next access.
        self._seed_split_cache()
        mock_fetch.return_value = self._matched_holding()
        update_scalable_etfs_in_portfolio(self.ctx)
        row = self.ctx.cache["IE0006WW1TQ4"]
        self.assertNotIn("countries", row)
        self.assertNotIn("sectors", row)
        self.assertEqual(row["price"], 40.315)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_mixed_flags_clear_only_unscraped_field(self, mock_fetch) -> None:
        self.ctx.config.fetch_geosplit = True
        self.ctx.config.fetch_sectorsplit = False
        self._seed_split_cache()
        mock_fetch.return_value = self._matched_holding()
        update_scalable_etfs_in_portfolio(self.ctx)
        row = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(row["countries"], {"United States": 0.9})
        self.assertNotIn("sectors", row)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_updates_cash_value_in_place(self, mock_fetch) -> None:
        self.ctx.portfolio[CASH_PORTFOLIO].append(
            {
                "name": "Cash",
                "value": 1.0,
                "broker": "scalable",
                "ISIN": None,
            }
        )
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
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
            _CASH_FETCH_KEY: ScalableHolding(
                isin=None,
                name="Cash",
                shares=None,
                value=12.5,
                price=None,
                is_cash=True,
            ),
        }
        update_scalable_etfs_in_portfolio(self.ctx)
        cash = [p for p in self.ctx.portfolio[CASH_PORTFOLIO] if p["name"] == "Cash"]
        self.assertEqual(len(cash), 1)
        self.assertEqual(cash[0]["value"], 12.5)
        self.assertIsNone(cash[0]["shares"])
        self.assertIsNone(cash[0]["ISIN"])

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_adds_cash_with_null_meta(self, mock_fetch) -> None:
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
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
            _CASH_FETCH_KEY: ScalableHolding(
                isin=None,
                name="Cash",
                shares=None,
                value=0.0,
                price=None,
                is_cash=True,
            ),
        }
        update_scalable_etfs_in_portfolio(self.ctx)
        cash = [p for p in self.ctx.portfolio[CASH_PORTFOLIO] if p["name"] == "Cash"]
        self.assertEqual(len(cash), 1)
        self.assertEqual(cash[0]["value"], 0.0)
        self.assertIsNone(cash[0]["ISIN"])
        self.assertIsNone(cash[0]["shares"])
        self.assertIsNone(cash[0]["dmem"])
        self.assertIsNone(cash[0]["dmem_other"])
        self.assertIsNone(cash[0]["usavn"])
        self.assertEqual(cash[0]["broker"], "scalable")
        self.assertNotIn("short_name", cash[0])

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_removes_cash_when_breakdown_absent(self, mock_fetch) -> None:
        self.ctx.portfolio[CASH_PORTFOLIO].append(
            {
                "name": "Cash",
                "value": 1.0,
                "broker": "scalable",
                "ISIN": None,
            }
        )
        mock_fetch.return_value = {
            "IE0006WW1TQ4": ScalableHolding(
                isin="IE0006WW1TQ4",
                name="Xtrackers",
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
        update_scalable_etfs_in_portfolio(self.ctx)
        names = [p["name"] for p in self.ctx.portfolio[CASH_PORTFOLIO]]
        self.assertNotIn("Cash", names)
        self.assertIn("Tagesgeld", names)

    @patch("scrape.scalable.fetch_scalable_etfs")
    def test_empty_fetch_leaves_portfolio_unchanged(self, mock_fetch) -> None:
        mock_fetch.return_value = {}
        with self.assertLogs("scrape.scalable", level="WARNING"):
            update_scalable_etfs_in_portfolio(self.ctx)
        self.assertEqual(len(self.ctx.portfolio[EQUITY_PORTFOLIO]), 1)
        self.assertEqual(self.ctx.portfolio[CASH_PORTFOLIO][0]["value"], 1.0)

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
        self.ctx.config.fetch_prices = True
        update_scalable_etfs_in_portfolio(self.ctx)
        self.assertEqual(self.ctx.cache["IE0006WW1TQ4"]["price"], 40.315)
        self.assertTrue(self.ctx.cache_dirty)
        self.assertNotIn("price", self.ctx.portfolio[EQUITY_PORTFOLIO][0])

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
        self.ctx.config.fetch_prices = False
        update_scalable_etfs_in_portfolio(self.ctx)
        # Cache should be marked dirty because sector cache is cleared
        # even when fetch_prices is False, to force sector refetch on next access.
        self.assertTrue(self.ctx.cache_dirty)
        # But price cache should not be populated
        for entry in self.ctx.cache.values():
            self.assertNotIn("price", entry)


if __name__ == "__main__":
    unittest.main()
