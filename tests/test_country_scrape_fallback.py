"""A failed JustETF country scrape warns and returns an empty list instead of aborting."""

from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from position.justetf_position import JustETFPosition
from utils import get_fetch_geosplit, set_fetch_geosplit

_ISIN = "LU1547515137"
_HTTP_403 = urllib.error.HTTPError(
    "https://www.justetf.com", 403, "Forbidden", {}, None
)


class TestJustETFCountryScrapeFailure(unittest.TestCase):
    def setUp(self) -> None:
        self._geo = get_fetch_geosplit()
        set_fetch_geosplit(True)

    def tearDown(self) -> None:
        set_fetch_geosplit(self._geo)

    def _position(self, error: Exception) -> JustETFPosition:
        with patch.object(
            JustETFPosition, "_fetch_countries_with_retries", side_effect=error
        ):
            with patch.object(JustETFPosition, "_fast_info_price", return_value=12.0):
                return JustETFPosition(_ISIN, name="Bond ETF", shares=10)

    def test_http_error_returns_empty_countries(self) -> None:
        self.assertEqual(self._position(_HTTP_403).countries(), [])

    def test_runtime_error_returns_empty_countries(self) -> None:
        pos = self._position(RuntimeError("JustETF HTTP 403"))
        self.assertEqual(pos.countries(), [])

    def test_failure_logs_warning(self) -> None:
        with self.assertLogs("position.justetf_position", level="WARNING") as logs:
            self._position(_HTTP_403)
        self.assertIn(_ISIN, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
