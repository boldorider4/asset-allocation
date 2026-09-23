# SPDX-License-Identifier: AGPL-3.0-or-later
"""DWS holdings JSON country aggregation and Xtrackers factory routing."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import BytesIO

from position.amundi_position import AmundiPosition
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.xtrackers_position import (
    XtrackersPosition,
    _DWS_PRODUCT_EXISTS,
    dws_product_url_exists,
)
from cli.context import AppConfig, RuntimeContext

_ISIN = "IE00BTJRMP35"
_SLUG = "IE00BTJRMP35-msci-emerging-markets-ucits-etf-1c"
_PRODUCT_FINAL = f"https://etf.dws.com/en-gb/{_SLUG}/"

_HOLDINGS = {
    "tables": [
        {
            "values": [
                {
                    "column_1": {"value": "15.00%", "sortValue": 15.0},
                    "column_3": {"value": "Taiwan"},
                    "column_4": {"value": "Information Technology"},
                },
                {
                    "column_1": {"value": "13.04%", "sortValue": 13.04},
                    "column_3": {"value": "Taiwan"},
                    "column_4": {"value": "Information Technology"},
                },
                {
                    "column_1": {"value": "21.77%", "sortValue": 21.77},
                    "column_3": {"value": "Korea, Republic of"},
                    "column_4": {"value": "Financials"},
                },
                {
                    "column_1": {"value": "2.00%", "sortValue": 2.0},
                    "column_3": {"value": "--"},
                    "column_4": {"value": "Unknown"},
                },
                {
                    "column_1": {"value": "1.00%", "sortValue": 1.0},
                    "column_3": {"value": "--"},
                    "column_4": {"value": "Consumer Discretionary"},
                },
                {
                    "column_1": {"value": "0.01%", "sortValue": 0.01},
                    "column_3": {"value": ""},
                    "column_4": {"value": "Energy"},
                },
            ]
        }
    ]
}


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


class TestHoldingsJsonAggregation(unittest.TestCase):
    def test_sums_by_country_and_aliases_korea(self) -> None:
        rows = XtrackersPosition._countries_from_holdings_json(_HOLDINGS)
        self.assertEqual(
            rows,
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "Other", "weight_pct": 3.0},
            ],
        )

    def test_empty_tables(self) -> None:
        self.assertEqual(XtrackersPosition._countries_from_holdings_json({"tables": []}), [])
        self.assertEqual(XtrackersPosition._countries_from_holdings_json({}), [])

    def test_parses_percent_text_when_sort_value_missing(self) -> None:
        payload = {
            "tables": [
                {
                    "values": [
                        {
                            "column_1": {"value": "10.5%"},
                            "column_3": {"value": "India"},
                        }
                    ]
                }
            ]
        }
        self.assertEqual(
            XtrackersPosition._countries_from_holdings_json(payload),
            [{"name": "India", "weight_pct": 10.5}],
        )


class TestHoldingsJsonSectorAggregation(unittest.TestCase):
    def test_sectors_from_holdings_json(self) -> None:
        rows = XtrackersPosition._sectors_from_holdings_json(_HOLDINGS)
        # Mapped to canonical: Information Technology->Technology, Financials->Finance,
        # Unknown->Other, Consumer Discretionary->Consumer, Energy->Commodities
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 28.04},
                {"name": "Finance", "weight_pct": 21.77},
                {"name": "Other", "weight_pct": 2.0},
                {"name": "Consumer", "weight_pct": 1.0},
                {"name": "Commodities", "weight_pct": 0.01},
            ],
        )

    def test_sectors_maps_unknown_to_other(self) -> None:
        payload = {
            "tables": [
                {
                    "values": [
                        {
                            "column_1": {"value": "10.0%", "sortValue": 10.0},
                            "column_4": {"value": "Unknown"},
                        }
                    ]
                }
            ]
        }
        rows = XtrackersPosition._sectors_from_holdings_json(payload)
        self.assertEqual(
            rows,
            [{"name": "Other", "weight_pct": 10.0}],
        )

    def test_sectors_empty_tables(self) -> None:
        self.assertEqual(XtrackersPosition._sectors_from_holdings_json({"tables": []}), [])
        self.assertEqual(XtrackersPosition._sectors_from_holdings_json({}), [])


class TestDwsProductUrl(unittest.TestCase):
    def setUp(self) -> None:
        _DWS_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _DWS_PRODUCT_EXISTS.clear()

    def test_slug_from_redirect_target(self) -> None:
        self.assertEqual(XtrackersPosition._slug_from_dws_product_url(_PRODUCT_FINAL), _SLUG)

    def test_exists_on_200(self) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=resp) as opener:
            self.assertTrue(dws_product_url_exists(_ISIN))
        opener.assert_called_once()

    def test_missing_isin_is_404(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://etf.dws.com/en-gb/XX0000000000", 404),
        ):
            self.assertFalse(dws_product_url_exists("XX0000000000"))

    def test_network_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertFalse(dws_product_url_exists(_ISIN))

    def test_timeout_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            self.assertFalse(dws_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with patch("urllib.request.urlopen", return_value=resp) as opener:
            self.assertTrue(dws_product_url_exists(_ISIN))
            self.assertTrue(dws_product_url_exists(_ISIN))
        opener.assert_called_once()


class TestXtrackersCountryFetch(unittest.TestCase):
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

    def test_aggregates_holdings_json(self) -> None:
        product = MagicMock()
        product.geturl.return_value = _PRODUCT_FINAL
        product.__enter__.return_value = product
        product.__exit__.return_value = False
        holdings = MagicMock()
        holdings.read.return_value = json.dumps(_HOLDINGS).encode()
        holdings.__enter__.return_value = holdings
        holdings.__exit__.return_value = False

        with patch(
            "urllib.request.urlopen", side_effect=[product, holdings]
        ):
            with patch.object(XtrackersPosition, "_fast_info_price", return_value=12.0):
                pos = XtrackersPosition(_ISIN, name="Xtrackers EM", shares=1, ctx=self.ctx)
        self.assertEqual(
            pos.countries(),
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "Other", "weight_pct": 3.0},
            ],
        )


class TestXtrackersFactoryRouting(unittest.TestCase):
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
            "name": "Xtrackers MSCI Emerging Markets",
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

    def test_allowlisted_isin_and_existing_url_use_xtrackers(self) -> None:
        with patch("position.factory.dws_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, XtrackersPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.dws_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="MSCI Emerging Markets UCITS ETF")
        self.assertIsInstance(pos, XtrackersPosition)

    def test_xtrackers_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.dws_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU0290358497",
                    name="Xtrackers II EUR Overnight Rate Swap UCITS ETF (Acc)",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, XtrackersPosition)

    def test_scalable_ac_world_stays_justetf(self) -> None:
        with patch("position.factory.dws_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU2903252349",
                    name="Scalable AC World Xtrackers UCITS ETF (Acc)",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, XtrackersPosition)

    def test_404_falls_back_to_justetf(self) -> None:
        with patch("position.factory.dws_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, XtrackersPosition)

    def test_amundi_does_not_use_xtrackers(self) -> None:
        with patch("position.factory.dws_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, XtrackersPosition)

    def test_without_fetch_geosplit_skips_dws_probe(self) -> None:
        self.ctx.config.fetch_geosplit = False
        with patch("position.factory.dws_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, XtrackersPosition)


if __name__ == "__main__":
    unittest.main()
