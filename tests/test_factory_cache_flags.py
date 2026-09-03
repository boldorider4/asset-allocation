"""Factory cache behavior for ``--fetch-prices`` and ``--fetch-geosplit``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from position.factory import factory
from position.cli_query_position import CLIQueryPosition
from position.justetf_position import JustETFPosition
from position.yfinance_position import YFinancePosition
import position.factory as factory_mod
from utils import (
    get_fetch_geosplit,
    get_fetch_oskar,
    get_fetch_prices,
    get_fetch_scalable,
    set_fetch_geosplit,
    set_fetch_oskar,
    set_fetch_prices,
    set_fetch_scalable,
)


class TestFactoryCacheFlags(unittest.TestCase):
    def setUp(self) -> None:
        self._prices = get_fetch_prices()
        self._geo = get_fetch_geosplit()
        self._scalable = get_fetch_scalable()
        self._oskar = get_fetch_oskar()
        self._source = factory_mod.POSITION_SOURCE
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache = Path(self._tmpdir.name) / "cache.json"
        self._cache.write_text(
            json.dumps(
                {
                    "IE0006WW1TQ4": {
                        "price": 10.0,
                        "countries": {"Germany": 0.4, "United States": 0.6},
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        set_fetch_prices(self._prices)
        set_fetch_geosplit(self._geo)
        set_fetch_scalable(self._scalable)
        set_fetch_oskar(self._oskar)
        factory_mod.POSITION_SOURCE = self._source
        self._tmpdir.cleanup()

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
        with patch("position.factory.CACHE_FILENAME", str(self._cache)):
            return factory(**defaults)

    def test_fetch_prices_without_scalable_uses_justetf_and_fast_info(self) -> None:
        set_fetch_prices(True)
        set_fetch_scalable(False)
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5) as fast:
            with patch.object(
                JustETFPosition,
                "_fetch_countries_with_retries",
                side_effect=AssertionError("must not scrape countries"),
            ):
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, CLIQueryPosition)
        fast.assert_called()
        self.assertEqual(pos.price, 99.5)
        saved = json.loads(self._cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE0006WW1TQ4"]["price"], 99.5)
        self.assertEqual(saved["IE0006WW1TQ4"]["countries"]["Germany"], 0.4)

    def test_fetch_scalable_uses_cli_query_position_not_fast_info(self) -> None:
        set_fetch_scalable(True)
        with patch.object(
            CLIQueryPosition,
            "_fast_info_price",
            side_effect=AssertionError("_fast_info_price should not be called"),
        ):
            pos = self._factory(broker="scalable")
        self.assertIsInstance(pos, CLIQueryPosition)
        self.assertEqual(pos.price, 40.315)

    def test_fetch_prices_uses_shares_times_price_not_cached_value(self) -> None:
        set_fetch_prices(True)
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(value=140.0, shares=4)
        self.assertEqual(pos.price, 99.5)
        self.assertEqual(pos.value, 398.0)

    def test_live_scalable_value_prevails_with_fetch_prices(self) -> None:
        set_fetch_scalable(True)
        set_fetch_prices(True)
        pos = self._factory(broker="scalable", value=140.0, shares=4, price=40.315)
        self.assertEqual(pos.price, 40.315)
        self.assertEqual(pos.value, 140.0)

    def test_live_oskar_value_prevails_with_fetch_prices(self) -> None:
        set_fetch_oskar(True)
        set_fetch_prices(True)
        with patch.object(JustETFPosition, "_fast_info_price", return_value=99.5):
            pos = self._factory(broker="oskar", value=140.0, shares=4)
        self.assertEqual(pos.value, 140.0)

    def test_without_fetch_prices_cached_value_prevails(self) -> None:
        set_fetch_prices(False)
        pos = self._factory(value=140.0, shares=4, price=10.0)
        self.assertEqual(pos.value, 140.0)

    def test_without_fetch_prices_uses_cache_and_skips_fast_info(self) -> None:
        set_fetch_prices(False)
        set_fetch_scalable(False)
        with patch.object(
            JustETFPosition,
            "_fast_info_price",
            side_effect=AssertionError("_fast_info_price should not be called"),
        ):
            pos = self._factory(price=None)
        self.assertEqual(pos.price, 10.0)

    def test_fetch_geosplit_scrapes_and_writes_countries(self) -> None:
        set_fetch_geosplit(True)
        set_fetch_prices(False)
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
        saved = json.loads(self._cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE0006WW1TQ4"]["countries"], {"France": 1.0})
        self.assertEqual(saved["IE0006WW1TQ4"]["price"], 10.0)

    def test_without_geosplit_missing_countries_does_not_scrape(self) -> None:
        self._cache.write_text(
            json.dumps({"IE0006WW1TQ4": {"price": 10.0}}),
            encoding="utf-8",
        )
        set_fetch_geosplit(False)
        with patch.object(
            JustETFPosition,
            "_fetch_countries_with_retries",
            side_effect=AssertionError("must not scrape countries"),
        ):
            pos = self._factory(price=None)
        self.assertEqual(pos.countries(), [])

    def test_yfinance_geosplit_does_not_become_justetf(self) -> None:
        factory_mod.POSITION_SOURCE = factory_mod.YFINANCE
        set_fetch_geosplit(True)
        set_fetch_scalable(False)
        with patch.object(YFinancePosition, "_fast_info_price", return_value=12.0):
            pos = self._factory(price=None, broker="other")
        self.assertIsInstance(pos, YFinancePosition)
        self.assertNotIsInstance(pos, JustETFPosition)
        with self.assertRaises(NotImplementedError):
            pos.countries()
        saved = json.loads(self._cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE0006WW1TQ4"]["countries"]["Germany"], 0.4)

    def test_countries_only_cache_row_fetches_price_without_caching_it(self) -> None:
        """A ``--fetch-geosplit`` row has no ``price``: fetch it, never store 0.0."""
        self._cache.write_text(
            json.dumps({"IE0006WW1TQ4": {"countries": {"France": 1.0}}}),
            encoding="utf-8",
        )
        before = self._cache.read_text(encoding="utf-8")
        set_fetch_prices(False)
        set_fetch_geosplit(False)
        set_fetch_scalable(False)
        with patch.object(JustETFPosition, "_fast_info_price", return_value=8.65) as fast:
            pos = self._factory(price=None)
        fast.assert_called()
        self.assertEqual(pos.price, 8.65)
        self.assertEqual(self._cache.read_text(encoding="utf-8"), before)

    def test_neither_flag_does_not_rewrite_cache(self) -> None:
        before = self._cache.read_text(encoding="utf-8")
        set_fetch_prices(False)
        set_fetch_geosplit(False)
        self._factory(price=None)
        self.assertEqual(self._cache.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
