"""Amundi ProductAPI country aggregation and factory routing."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from position.amundi_position import (
    AmundiPosition,
    _AMUNDI_PRODUCT_EXISTS,
    amundi_product_url_exists,
)
from position.factory import factory
from position.justetf_position import JustETFPosition
from context import AppConfig, RuntimeContext

_ISIN = "IE000BI8OT95"
_PRODUCTS = {
    "products": [
        {
            "productId": _ISIN,
            "characteristics": {"ISIN": _ISIN},
            "breakDowns": [
                {
                    "aggregationField": "FUND_TOP10",
                    "breakDownData": [],
                },
                {
                    "aggregationField": "FUND_COUNTRIES",
                    "breakDownData": [
                        {"aggregationName": "United States", "weight": 0.72},
                        {"aggregationName": "Taiwan", "weight": 0.15},
                        {"aggregationName": "Taiwan", "weight": 0.1304},
                        {
                            "aggregationName": "Korea, Republic of",
                            "weight": 0.2177,
                        },
                        {"aggregationName": "", "weight": 0.01},
                        {"aggregationName": "UAE", "weight": 0.0},
                    ],
                },
            ],
        }
    ]
}

_PRODUCTS_WITH_SECTORS = {
    "products": [
        {
            "productId": _ISIN,
            "characteristics": {"ISIN": _ISIN},
            "breakDowns": [
                {
                    "aggregationField": "FUND_COUNTRIES",
                    "breakDownData": [
                        {"aggregationName": "United States", "weight": 0.72},
                        {"aggregationName": "Taiwan", "weight": 0.15},
                    ],
                },
                {
                    "aggregationField": "FUND_SECTORS",
                    "breakDownData": [
                        {"aggregationName": "Information Technology", "weight": 0.25},
                        {"aggregationName": "Financials", "weight": 0.20},
                        {"aggregationName": "Health Care", "weight": 0.15},
                        {"aggregationName": "Consumer Discretionary", "weight": 0.10},
                        {"aggregationName": "Communication Services", "weight": 0.08},
                        {"aggregationName": "Consumer Staples", "weight": 0.07},
                        {"aggregationName": "Energy", "weight": 0.05},
                        {"aggregationName": "Others", "weight": 0.04},
                        {"aggregationName": "Industrials", "weight": 0.03},
                        {"aggregationName": "Materials", "weight": 0.02},
                    ],
                },
            ],
        }
    ]
}


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _json_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": "application/json"}
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestFundCountriesAggregation(unittest.TestCase):
    def test_sums_by_country_and_aliases(self) -> None:
        rows = AmundiPosition._countries_from_products_json(_PRODUCTS, _ISIN)
        self.assertEqual(
            rows,
            [
                {"name": "United States", "weight_pct": 72.0},
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "UAE", "weight_pct": 0.0},
            ],
        )

    def test_empty_products(self) -> None:
        self.assertEqual(
            AmundiPosition._countries_from_products_json({"products": []}, _ISIN),
            [],
        )
        self.assertEqual(AmundiPosition._countries_from_products_json({}, _ISIN), [])

    def test_uses_adjusted_weight_when_weight_missing(self) -> None:
        payload = {
            "products": [
                {
                    "productId": _ISIN,
                    "breakDowns": [
                        {
                            "aggregationField": "FUND_COUNTRIES",
                            "breakDownData": [
                                {
                                    "aggregationName": "France",
                                    "adjustedWeight": 0.105,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        self.assertEqual(
            AmundiPosition._countries_from_products_json(payload, _ISIN),
            [{"name": "France", "weight_pct": 10.5}],
        )


class TestFundSectorsAggregation(unittest.TestCase):
    def test_sectors_from_products_json(self) -> None:
        rows = AmundiPosition._sectors_from_products_json(_PRODUCTS_WITH_SECTORS, _ISIN)
        # Mapped to canonical names: Information Technology->Technology, Financials->Finance,
        # Health Care->Healthcare, Consumer Discretionary->Consumer, Communication Services->Telecommunication,
        # Consumer Staples->Consumer, Energy->Commodities, Others->Other
        # Sorted by weight descending: Technology(25), Finance(20), Consumer(17), Healthcare(15), ...
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 25.0},
                {"name": "Finance", "weight_pct": 20.0},
                {"name": "Consumer", "weight_pct": 17.0},  # Consumer Discretionary + Consumer Staples
                {"name": "Healthcare", "weight_pct": 15.0},
                {"name": "Telecommunication", "weight_pct": 8.0},
                {"name": "Commodities", "weight_pct": 5.0},
                {"name": "Other", "weight_pct": 4.0},
                {"name": "Industrials", "weight_pct": 3.0},
                {"name": "Materials", "weight_pct": 2.0},
            ],
        )

    def test_sectors_maps_others_to_other(self) -> None:
        payload = {
            "products": [
                {
                    "productId": _ISIN,
                    "breakDowns": [
                        {
                            "aggregationField": "FUND_SECTORS",
                            "breakDownData": [
                                {"aggregationName": "Others", "weight": 0.10},
                                {"aggregationName": "Information Technology", "weight": 0.90},
                            ],
                        }
                    ],
                }
            ]
        }
        rows = AmundiPosition._sectors_from_products_json(payload, _ISIN)
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 90.0},
                {"name": "Other", "weight_pct": 10.0},
            ],
        )

    def test_sectors_empty_products(self) -> None:
        self.assertEqual(
            AmundiPosition._sectors_from_products_json({"products": []}, _ISIN),
            [],
        )
        self.assertEqual(AmundiPosition._sectors_from_products_json({}, _ISIN), [])

    def test_sectors_missing_field_returns_empty(self) -> None:
        payload = {
            "products": [
                {
                    "productId": _ISIN,
                    "breakDowns": [
                        {
                            "aggregationField": "FUND_COUNTRIES",
                            "breakDownData": [{"aggregationName": "US", "weight": 1.0}],
                        }
                    ],
                }
            ]
        }
        self.assertEqual(AmundiPosition._sectors_from_products_json(payload, _ISIN), [])


class TestAmundiProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _AMUNDI_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _AMUNDI_PRODUCT_EXISTS.clear()

    def test_exists_on_matching_product(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response({"products": [{"productId": _ISIN}]}),
        ) as opener:
            self.assertTrue(amundi_product_url_exists(_ISIN))
        opener.assert_called_once()

    def test_empty_products_is_missing(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response({"products": []}),
        ):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_html_200_is_not_json(self) -> None:
        resp = _json_response({"products": [{"productId": _ISIN}]})
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        with patch("urllib.request.urlopen", return_value=resp):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(
                "https://www.amundietf.de/mapi/ProductAPI/getProductsData",
                404,
            ),
        ):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(amundi_product_url_exists(""))
        opener.assert_not_called()

    def test_network_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_timeout_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_connection_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=ConnectionError("Connection reset by peer"),
        ):
            self.assertFalse(amundi_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response({"products": [{"productId": _ISIN}]}),
        ) as opener:
            self.assertTrue(amundi_product_url_exists(_ISIN))
            self.assertTrue(amundi_product_url_exists(_ISIN))
        opener.assert_called_once()


class TestAmundiCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_prices=False,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True

    def test_aggregates_fund_countries(self) -> None:
        with patch("urllib.request.urlopen", return_value=_json_response(_PRODUCTS)):
            with patch.object(AmundiPosition, "_fast_info_price", return_value=12.0):
                pos = AmundiPosition(
                    _ISIN, name="Amundi Core MSCI World", shares=1, ctx=self.ctx
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "United States", "weight_pct": 72.0},
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "UAE", "weight_pct": 0.0},
            ],
        )

    def test_timeout_falls_back_to_empty_countries(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            with patch.object(AmundiPosition, "_fast_info_price", return_value=12.0):
                pos = AmundiPosition(
                    _ISIN, name="Amundi Core MSCI World", shares=1, ctx=self.ctx
                )
        self.assertEqual(pos.countries(), [])


class TestAmundiFactoryRouting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_prices=False,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True

    def _factory(self, **kwargs):
        defaults = {
            "isin": _ISIN,
            "name": "Amundi Core MSCI World UCITS ETF (Acc)",
            "shares": 1,
            "price": 10.0,
        }
        defaults.update(kwargs)
        defaults["ctx"] = self.ctx
        return factory(**defaults)

    def _no_country_scrape(self):
        return patch.object(
            JustETFPosition, "_fetch_countries_with_retries", return_value=[]
        )

    def test_allowlisted_isin_and_existing_url_use_amundi(self) -> None:
        with patch("position.factory.amundi_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, AmundiPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.amundi_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="Core MSCI World UCITS ETF")
        self.assertIsInstance(pos, AmundiPosition)

    def test_amundi_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.amundi_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU0290358497",
                    name="Amundi Prime Euro Gov Overnight",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, AmundiPosition)

    def test_missing_product_falls_back_to_justetf(self) -> None:
        with patch("position.factory.amundi_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, AmundiPosition)

    def test_probe_timeout_falls_back_to_justetf(self) -> None:
        # Regression test for a probe read-timeout killing the whole run:
        # the real probe must swallow it and the factory must fall back.
        from position.amundi_position import _AMUNDI_PRODUCT_EXISTS

        _AMUNDI_PRODUCT_EXISTS.clear()
        try:
            with patch(
                "urllib.request.urlopen",
                side_effect=TimeoutError("The read operation timed out"),
            ):
                with self._no_country_scrape():
                    pos = self._factory()
        finally:
            _AMUNDI_PRODUCT_EXISTS.clear()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, AmundiPosition)

    def test_without_fetch_geosplit_skips_amundi_probe(self) -> None:
        self.ctx.config.fetch_geosplit = False
        with patch("position.factory.amundi_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, AmundiPosition)


if __name__ == "__main__":
    unittest.main()
