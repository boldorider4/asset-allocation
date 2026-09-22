# SPDX-License-Identifier: AGPL-3.0-or-later
"""Factory cache behavior for ``--fetch-prices`` and ``--fetch-geosplit``."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from context import AppConfig, RuntimeContext
from portfolio.portfolio import Portfolio
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.yfinance_position import YFinancePosition
from utils import (
    persist_fetched_values_in_portfolio,
    persist_oskar_shares_in_portfolio,
)


class TestFactoryCacheFlags(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {
            "IE0006WW1TQ4": {
                "price": 10.0,
                "countries": {"Germany": 0.4, "United States": 0.6},
            }
        }
        self.ctx.cache_loaded = True
        self._exists_patch = patch(
            "position.factory.dws_product_url_exists", return_value=True
        )
        self._exists_patch.start()
        self.addCleanup(self._exists_patch.stop)

    def _factory(self, **kwargs):
        defaults = {
            "isin": "IE0006WW1TQ4",
            "name": "Xtrackers",
            "shares": 4,
            "value": None,
            "broker": "other",
            "price": 40.315,
        }
        defaults.update(kwargs)
        defaults["ctx"] = self.ctx
        return factory(**defaults)

    def test_fetch_prices_without_scalable_uses_justetf_and_fast_info(self) -> None:
        self.ctx.config.fetch_prices = True
        self.ctx.config.fetch_scalable = False
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5) as fast:
            with patch.object(
                JustETFPosition,
                "_fetch_countries_with_retries",
                side_effect=AssertionError("must not scrape countries"),
            ):
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        fast.assert_called()
        self.assertEqual(pos.price, 99.5)
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["price"], 99.5)
        self.assertEqual(saved["countries"]["Germany"], 0.4)

    def test_fetch_scalable_uses_supplied_price_not_fast_info(self) -> None:
        self.ctx.config.fetch_scalable = True
        with patch.object(
            JustETFPosition,
            "_fast_info_price",
            side_effect=AssertionError("_fast_info_price should not be called"),
        ):
            pos = self._factory(broker="scalable")
        self.assertIsInstance(pos, JustETFPosition)
        self.assertEqual(pos.price, 40.315)

    def test_fetch_prices_uses_shares_times_price_not_cached_value(self) -> None:
        self.ctx.config.fetch_prices = True
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(value=140.0, shares=4)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.value, 398.0)

    def test_live_scalable_value_prevails_with_fetch_prices(self) -> None:
        self.ctx.config.fetch_scalable = True
        self.ctx.config.fetch_prices = True
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(broker="scalable", value=140.0, shares=4, price=40.315)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.value, 140.0)

    def test_live_traderepublic_value_prevails_with_fetch_prices(self) -> None:
        self.ctx.config.fetch_traderepublic = True
        self.ctx.config.fetch_prices = True
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(
                broker="traderepublic", value=140.0, shares=4, price=40.315
            )
        self.assertIsInstance(pos, JustETFPosition)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.value, 140.0)

    def test_scraped_value_prevails_without_fetch_flags(self) -> None:
        """A value an earlier scrape wrote wins over shares × cached price."""
        self.ctx.config.fetch_prices = False
        self.ctx.config.fetch_scalable = False
        for broker in ("scalable", "traderepublic", "oskar"):
            with self.subTest(broker=broker):
                pos = self._factory(
                    broker=broker, value=140.0, shares=4, price=None
                )
                self.assertEqual(pos.price, 10.0)
                self.assertEqual(pos.shares, 4)
                self.assertEqual(pos.value, 140.0)

    def test_fetch_prices_alone_requotes_broker_row(self) -> None:
        """``--fetch-prices`` without the broker flag asks for shares × quote."""
        self.ctx.config.fetch_prices = True
        self.ctx.config.fetch_scalable = False
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 4,
                "value": 140.0,
                "broker": "scalable",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                pos = self._factory(
                    broker="scalable", value=140.0, shares=4, price=40.315
                )
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.value, 398.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["value"], 398.0)
        write.assert_called_once()
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["price"], 99.5)

    def test_fetch_prices_updates_cache_even_when_value_prevails(self) -> None:
        self.ctx.config.fetch_scalable = True
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 4,
                "value": 140.0,
                "broker": "scalable",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                pos = self._factory(broker="scalable", value=140.0, shares=4, price=40.315)
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.value, 140.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["value"], 140.0)
        write.assert_not_called()
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["price"], 99.5)

    def test_live_oskar_value_prevails_with_fetch_prices(self) -> None:
        self.ctx.config.fetch_oskar = True
        self.ctx.config.fetch_prices = True
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(broker="oskar", value=140.0, shares=4)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.shares, 4)
        self.assertEqual(pos.value, 140.0)

    def test_oskar_fetch_prices_queues_shares_for_batch_write(self) -> None:
        self.ctx.config.fetch_oskar = True
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": None,
                "value": 199.0,
                "broker": "oskar",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                pos = self._factory(
                    broker="oskar", value=199.0, shares=None, price=None
                )
            self.assertIsNone(
                self.ctx.portfolio["equity_portfolio"][0]["shares"]
            )
            persist_oskar_shares_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.shares, 199.0 / 99.5)
        self.assertEqual(pos.value, 199.0)
        row = self.ctx.portfolio["equity_portfolio"][0]
        # The fetched quote belongs in cache.json only, never in the asset file.
        self.assertNotIn("price", row)
        self.assertEqual(row["shares"], 199.0 / 99.5)
        write.assert_called_once()
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["price"], 99.5)

    def test_oskar_shares_only_persisted_on_matching_oskar_row(self) -> None:
        self.ctx.config.fetch_oskar = True
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": None,
                "value": 199.0,
                "broker": "oskar",
            },
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 7,
                "value": 700.0,
                "broker": "scalable",
            },
            {
                "name": "Other OSKAR ETF",
                "ISIN": "IE000OTHER00",
                "shares": None,
                "value": 50.0,
                "broker": "oskar",
            },
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                self._factory(broker="oskar", value=199.0, shares=None, price=None)
                self._factory(
                    isin="IE000OTHER00",
                    name="Other OSKAR ETF",
                    broker="oskar",
                    value=50.0,
                    shares=None,
                    price=None,
                )
            self.assertIsNone(self.ctx.portfolio["equity_portfolio"][0]["shares"])
            self.assertIsNone(self.ctx.portfolio["equity_portfolio"][2]["shares"])
            persist_oskar_shares_in_portfolio(self.ctx)
        oskar_row, scalable_row, other_row = self.ctx.portfolio["equity_portfolio"]
        self.assertEqual(oskar_row["shares"], 199.0 / 99.5)
        self.assertEqual(scalable_row["shares"], 7)
        self.assertEqual(other_row["shares"], 50.0 / 99.5)
        write.assert_called_once()

    def test_oskar_estimates_shares_from_cached_price(self) -> None:
        self.ctx.config.fetch_oskar = True
        self.ctx.config.fetch_prices = False
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": None,
                "value": 199.0,
                "broker": "oskar",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(
                JustETFPosition,
                "_fast_info_price",
                side_effect=AssertionError("_fast_info_price should not be called"),
            ):
                pos = self._factory(broker="oskar", value=199.0, shares=None, price=None)
            persist_oskar_shares_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 10.0)
        self.assertEqual(pos.shares, 199.0 / 10.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["shares"], 19.9)
        write.assert_called_once()

    def test_oskar_does_not_estimate_shares_when_quote_missing(self) -> None:
        self.ctx.config.fetch_oskar = True
        self.ctx.config.fetch_prices = True
        self.ctx.cache = {}
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=None):
                pos = self._factory(
                    broker="oskar", value=199.0, shares=None, price=None
                )
            persist_oskar_shares_in_portfolio(self.ctx)
        self.assertIsNone(pos.price)
        self.assertIsNone(pos.shares)
        write.assert_not_called()

    def test_oskar_does_not_estimate_shares_without_live_scrape(self) -> None:
        self.ctx.config.fetch_oskar = False
        self.ctx.config.fetch_prices = True
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                pos = self._factory(
                    broker="oskar", value=199.0, shares=None, price=None
                )
            persist_oskar_shares_in_portfolio(self.ctx)
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 99.5)
        self.assertIsNone(pos.shares)
        write.assert_not_called()

    def test_fetch_prices_oskar_falls_back_to_cached_quote_when_live_missing(self) -> None:
        self.ctx.config.fetch_oskar = False
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 2,
                "value": 140.0,
                "broker": "oskar",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=None):
                pos = self._factory(
                    broker="oskar", value=140.0, shares=2, price=None
                )
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 10.0)
        self.assertEqual(pos.value, 20.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["value"], 20.0)
        write.assert_called_once()

    def test_fetch_prices_without_oskar_updates_asset_value_from_shares(self) -> None:
        self.ctx.config.fetch_oskar = False
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 2,
                "value": 140.0,
                "broker": "oskar",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                pos = self._factory(
                    broker="oskar", value=140.0, shares=2, price=None
                )
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.value, 199.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["value"], 199.0)
        write.assert_called_once()

    def test_fetch_prices_without_oskar_portfolio_persists_estimated_shares(self) -> None:
        self.ctx.config.fetch_oskar = False
        self.ctx.config.fetch_prices = True
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 199.0 / 99.5,
                "value": 140,
                "broker": "oskar",
                "dmem": 1,
                "dmem_other": 1,
                "usavn": 0.5,
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
                port = Portfolio(
                    "equity",
                    positions=self.ctx.portfolio["equity_portfolio"],
                    ctx=self.ctx,
                )
            persist_oskar_shares_in_portfolio(self.ctx)
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(port.value, 199.0)
        self.assertEqual(self.ctx.portfolio["equity_portfolio"][0]["value"], 199.0)
        write.assert_called_once()

    def test_without_fetch_prices_cached_value_prevails(self) -> None:
        self.ctx.config.fetch_prices = False
        pos = self._factory(value=140.0, shares=4, price=10.0)
        # Live scrape value is not preferred, so holdings value is shares × quote.
        self.assertEqual(pos.value, 40.0)

    def test_without_fetch_prices_uses_cache_and_skips_fast_info(self) -> None:
        self.ctx.config.fetch_prices = False
        self.ctx.config.fetch_scalable = False
        with patch.object(
            JustETFPosition,
            "_fast_info_price",
            side_effect=AssertionError("_fast_info_price should not be called"),
        ):
            pos = self._factory(price=None)
        self.assertEqual(pos.price, 10.0)

    def test_fetch_geosplit_scrapes_and_writes_countries(self) -> None:
        self.ctx.config.fetch_geosplit = True
        self.ctx.config.fetch_prices = False
        sample = [{"name": "France", "weight_pct": 100.0}]
        with patch.object(
            JustETFPosition, "_fetch_countries_with_retries", return_value=sample
        ) as mocked:
            with patch.object(
                JustETFPosition,
                "_fast_info_price",
                side_effect=AssertionError("must not scrape price"),
            ):
                pos = self._factory(price=None)
        mocked.assert_called()
        self.assertEqual(pos.countries(), sample)
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["countries"], {"France": 1.0})
        self.assertEqual(saved["price"], 10.0)

    def test_without_geosplit_missing_countries_does_not_scrape(self) -> None:
        self.ctx.cache = {"IE0006WW1TQ4": {"price": 10.0}}
        self.ctx.config.fetch_geosplit = False
        with patch.object(
            JustETFPosition,
            "_fetch_countries_with_retries",
            side_effect=AssertionError("must not scrape countries"),
        ):
            pos = self._factory(price=None)
        self.assertEqual(pos.countries(), [])

    def test_yfinance_geosplit_does_not_become_justetf(self) -> None:
        self.ctx.config.position_source = "yfinance"
        self.ctx.config.fetch_geosplit = True
        self.ctx.config.fetch_scalable = False
        with patch.object(YFinancePosition, "_fast_info_price", return_value=12.0):
            with patch(
                "position.factory.ssga_product_url_exists", return_value=False
            ):
                pos = self._factory(
                    price=None,
                    broker="other",
                    isin="IE00B4YBJ215",
                    name="iShares Core MSCI World UCITS ETF",
                )
        self.assertIsInstance(pos, YFinancePosition)
        self.assertNotIsInstance(pos, JustETFPosition)
        with self.assertRaises(NotImplementedError):
            pos.countries()
        saved = self.ctx.cache["IE0006WW1TQ4"]
        self.assertEqual(saved["countries"]["Germany"], 0.4)

    def test_countries_only_cache_row_backfills_missing_price(self) -> None:
        """A ``--fetch-geosplit`` row has no ``price``: fetch it and store it."""
        self.ctx.cache = {"IE0006WW1TQ4": {"countries": {"France": 1.0}}}
        self.ctx.config.fetch_prices = False
        self.ctx.config.fetch_geosplit = False
        self.ctx.config.fetch_scalable = False
        with patch.object(JustETFPosition, "_fast_info_price", return_value=8.65) as fast:
            pos = self._factory(price=None)
        fast.assert_called()
        self.assertEqual(pos.price, 8.65)
        self.assertEqual(
            self.ctx.cache,
            {"IE0006WW1TQ4": {"countries": {"France": 1.0}, "price": 8.65}},
        )
        self.assertTrue(self.ctx.cache_dirty)

    def test_nulled_value_row_uses_shares_times_cached_price(self) -> None:
        """A missing file value resolves to shares × quote."""
        self.ctx.config.fetch_prices = False
        self.ctx.config.fetch_scalable = False
        self.ctx.portfolio.clear()
        self.ctx.portfolio["equity_portfolio"] = [
            {
                "name": "Xtrackers",
                "ISIN": "IE0006WW1TQ4",
                "shares": 4,
                "value": None,
                "broker": "scalable",
            }
        ]
        with patch("utils.write_portfolio") as write:
            with patch.object(
                JustETFPosition,
                "_fast_info_price",
                side_effect=AssertionError("must use cached price"),
            ):
                pos = self._factory(broker="scalable", value=None, shares=4, price=None)
            persist_fetched_values_in_portfolio(self.ctx)
        self.assertEqual(pos.price, 10.0)
        self.assertEqual(pos.value, 40.0)
        write.assert_not_called()

    def test_neither_flag_does_not_rewrite_cache(self) -> None:
        before = dict(self.ctx.cache)
        self.ctx.config.fetch_prices = False
        self.ctx.config.fetch_geosplit = False
        self._factory(price=None)
        self.assertEqual(self.ctx.cache, before)
        self.assertFalse(self.ctx.cache_dirty)

    def test_cancel_event_aborts_before_any_network(self) -> None:
        import threading

        from position.factory import UpdateCancelled

        self.ctx.cancel_event = threading.Event()
        self.ctx.cancel_event.set()
        with patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("must not touch the network"),
        ):
            with self.assertRaises(UpdateCancelled):
                self._factory()


if __name__ == "__main__":
    unittest.main()
