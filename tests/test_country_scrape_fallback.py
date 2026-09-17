"""A failed JustETF country scrape warns and returns an empty list instead of aborting."""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from context import AppConfig, RuntimeContext
from position.justetf_position import JustETFPosition

_ISIN = "LU1547515137"
_HTTP_403 = urllib.error.HTTPError(
    "https://www.justetf.com", 403, "Forbidden", {}, None
)


class TestJustETFCountryScrapeFailure(unittest.TestCase):
    def setUp(self) -> None:
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True

    def _position(self, error: Exception) -> JustETFPosition:
        with patch.object(
            JustETFPosition, "_fetch_countries_with_retries", side_effect=error
        ):
            with patch.object(JustETFPosition, "_fast_info_price", return_value=12.0):
                return JustETFPosition(
                    _ISIN, name="Bond ETF", shares=10, ctx=self.ctx
                )

    def test_http_error_returns_empty_countries(self) -> None:
        self.assertEqual(self._position(_HTTP_403).countries(), [])

    def test_runtime_error_returns_empty_countries(self) -> None:
        pos = self._position(RuntimeError("JustETF HTTP 403"))
        self.assertEqual(pos.countries(), [])

    def test_timeout_error_returns_empty_countries(self) -> None:
        self.assertEqual(
            self._position(TimeoutError("The read operation timed out")).countries(),
            [],
        )

    def test_retry_then_success_on_timeout(self) -> None:
        sample = [{"name": "France", "weight_pct": 100.0}]
        with patch.object(
            JustETFPosition,
            "_http_country_dist_json",
            side_effect=[TimeoutError("The read operation timed out"), sample],
        ) as dist:
            with patch.object(JustETFPosition, "_fast_info_price", return_value=12.0):
                pos = JustETFPosition(
                    _ISIN, name="Bond ETF", shares=10, ctx=self.ctx
                )
        self.assertEqual(dist.call_count, 2)
        self.assertEqual(pos.countries(), sample)

    def test_price_lookup_failure_uses_supplied_price(self) -> None:
        tmp = Path(self._holder.name)
        ctx = RuntimeContext(
            config=AppConfig(
                fetch_prices=True,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        ctx.cache = {}
        ctx.cache_loaded = True
        with patch.object(
            JustETFPosition, "_fast_info_price", side_effect=RuntimeError("boom")
        ):
            pos = JustETFPosition(
                _ISIN, name="Bond ETF", shares=10, price=12.0, ctx=ctx
            )
        self.assertEqual(pos.price, 12.0)

    def test_price_timeout_uses_supplied_price(self) -> None:
        tmp = Path(self._holder.name)
        ctx = RuntimeContext(
            config=AppConfig(
                fetch_prices=True,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        ctx.cache = {}
        ctx.cache_loaded = True
        with patch.object(
            JustETFPosition,
            "_fast_info_price",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            pos = JustETFPosition(
                _ISIN, name="Bond ETF", shares=10, price=12.0, ctx=ctx
            )
        self.assertEqual(pos.price, 12.0)

    def test_failure_logs_warning(self) -> None:
        with self.assertLogs("position.justetf_position", level="WARNING") as logs:
            self._position(_HTTP_403)
        self.assertIn(_ISIN, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
