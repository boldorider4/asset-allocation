"""Unit tests for TradeRepublicPosition pricing and inherited country lookup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from position.factory import factory
from position.traderepublic_position import TradeRepublicPosition
from utils import (
    get_fetch_prices,
    get_fetch_traderepublic,
    set_fetch_prices,
    set_fetch_traderepublic,
)


class TestTradeRepublicPosition(unittest.TestCase):
    def test_supplied_price_skips_fast_info(self) -> None:
        with patch.object(
            TradeRepublicPosition,
            "_fast_info_price",
            side_effect=AssertionError("_fast_info_price should not be called"),
        ):
            pos = TradeRepublicPosition(
                "IE0006WW1TQ4",
                name="Xtrackers",
                shares=4,
                value=140.0,
                broker="traderepublic",
                price=40.315,
                cached_countries={"United States": 0.6, "Germany": 0.4},
            )
        self.assertEqual(pos.price, 40.315)
        self.assertEqual(pos.value, 140.0)

    def test_countries_uses_justetf_parent(self) -> None:
        sample = [{"name": "United States", "weight_pct": 55.0}]
        with patch.object(
            TradeRepublicPosition,
            "_fetch_countries_with_retries",
            return_value=sample,
        ) as mocked:
            pos = TradeRepublicPosition(
                "IE0006WW1TQ4",
                broker="traderepublic",
                price=40.315,
            )
            self.assertEqual(pos.countries(), sample)
            mocked.assert_called()

    def test_fast_info_raises_if_invoked(self) -> None:
        pos = TradeRepublicPosition.__new__(TradeRepublicPosition)
        with self.assertRaises(NotImplementedError):
            pos._fast_info_price()


class TestTradeRepublicFactoryCache(unittest.TestCase):
    def setUp(self) -> None:
        self._fetch = get_fetch_traderepublic()
        self._prices = get_fetch_prices()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache = Path(self._tmpdir.name) / "cache.json"
        self._cache.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        set_fetch_traderepublic(self._fetch)
        set_fetch_prices(self._prices)
        self._tmpdir.cleanup()

    def test_live_scan_writes_scanned_price_to_cache(self) -> None:
        set_fetch_traderepublic(True)
        set_fetch_prices(True)
        with patch("position.factory.CACHE_FILENAME", str(self._cache)):
            with patch.object(
                TradeRepublicPosition,
                "_fetch_countries_with_retries",
                return_value=[],
            ):
                pos = factory(
                    "IE0006WW1TQ4",
                    name="Xtrackers",
                    shares=4,
                    value=140.0,
                    broker="traderepublic",
                    price=40.315,
                )
        self.assertEqual(pos.price, 40.315)
        saved = json.loads(self._cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE0006WW1TQ4"]["price"], 40.315)


if __name__ == "__main__":
    unittest.main()
