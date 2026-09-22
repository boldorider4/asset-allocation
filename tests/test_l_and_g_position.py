# SPDX-License-Identifier: AGPL-3.0-or-later
"""L&G fund-centre Country (%) aggregation and factory routing."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from position.amundi_position import AmundiPosition
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.l_and_g_position import (
    LAndGPosition,
    _LANDG_PRODUCT_EXISTS,
    _LANDG_SHARECLASS,
    _LANDG_SECTOR_PART_ID,
    _LANDG_PORTFOLIO_PART_ID,
    landg_product_url_exists,
)
from context import AppConfig, RuntimeContext

_ISIN = "IE000Z9UVQ99"
_LISTING = {
    "metadata": {
        "share_class_fields": [
            {"code_name": "shareclassPageURL"},
            {"code_name": "ter"},
            {"code_name": "shareclassISIN"},
        ]
    },
    "funds": [
        {
            "id": 2137,
            "share_classes": [
                {
                    "id": 6908,
                    "data": [
                        "/en/de/adviser-wealth/fund-centre/ETF/Asia-Pacific-ex-Japan-ESG-Exclusions-Paris-Aligned/IE000Z9UVQ99/",
                        "0.16",
                        _ISIN,
                    ],
                }
            ],
        }
    ],
}
_COUNTRY_ROWS = [
    ["Australia", "61.0"],
    ["Hong Kong", "15.0"],
    ["Singapore", "13.7"],
    ["New Zealand", "4.4"],
    ["Cayman Islands", "4.1"],
    ["United States", "1.2"],
    ["Bermuda", "0.6"],
    ["Cash", "0.01"],
]

_SECTOR_ROWS = [
    ["Financials", "44.9"],
    ["Real Estate", "17.9"],
    ["Industrials", "11.7"],
    ["Health Care", "8.5"],
    ["Consumer Discretionary", "6.1"],
    ["Consumer Staples", "4.5"],
    ["Communication Services", "2.8"],
    ["Materials", "1.6"],
    ["Utilities", "1.6"],
    ["Information Technology", "0.4"],
]


def _sector_html(rows: list[list[str]] | None = None) -> bytes:
    payload = json.dumps(rows if rows is not None else _SECTOR_ROWS)
    return (
        "<div>"
        '<data data-key="sector" data-title="Sector (%)" data-component="sector">'
        '<div data-part_id="12761">'
        f'<script type="application/json" class="data">{payload}</script>'
        "</div>"
        '<div data-part_id="12602">'
        '<script type="application/json" class="data">[]</script>'
        "</div>"
        "</data>"
        "</div>"
    ).encode()


def _portfolio_html(rows: list[list[str]] | None = None) -> bytes:
    payload = json.dumps(rows if rows is not None else _COUNTRY_ROWS)
    return (
        "<div>"
        '<data data-key="country" data-title="Country (%)" data-component="country">'
        '<div data-part_id="12761">'
        f'<script type="application/json" class="data">{payload}</script>'
        "</div>"
        '<div data-part_id="12602">'
        '<script type="application/json" class="data">[]</script>'
        "</div>"
        "</data>"
        "</div>"
    ).encode()


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _response(body: bytes, status: int = 200, content_type: str = "application/json") -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _urlopen_listing_then_part(listing: dict, part_html: bytes):
    listing_body = json.dumps(listing).encode()

    def opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "fund-centre/" in url and "part?" not in url:
            return _response(listing_body)
        return _response(part_html, content_type="text/html")

    return opener


class TestLandGCountryHtml(unittest.TestCase):
    def test_part_url_expands_all_ids(self) -> None:
        from position.l_and_g_position import _part_url

        url = _part_url(
            {
                "fund_id": 2137,
                "share_class_id": 6908,
                "audience": 148,
                "route": 6696,
                "language": 1,
                "part_id": 12618,
            }
        )
        self.assertIn("id=12618", url)
        self.assertIn("route=6696", url)
        self.assertIn("fund_id=2137", url)
        self.assertIn("share_class_id=6908", url)
        self.assertNotIn("{", url)

    def test_sums_by_country_and_aliases(self) -> None:
        rows = LAndGPosition._countries_from_portfolio_html(
            _portfolio_html().decode()
        )
        self.assertEqual(
            rows,
            [
                {"name": "Australia", "weight_pct": 61.0},
                {"name": "Hong Kong", "weight_pct": 15.0},
                {"name": "Singapore", "weight_pct": 13.7},
                {"name": "New Zealand", "weight_pct": 4.4},
                {"name": "Cayman Islands", "weight_pct": 4.1},
                {"name": "United States", "weight_pct": 1.2},
                {"name": "Bermuda", "weight_pct": 0.6},
                {"name": "Other", "weight_pct": 0.01},
            ],
)
    
    def test_skips_empty_country_tables(self) -> None:
        self.assertEqual(
            LAndGPosition._countries_from_portfolio_html("<html></html>"),
            [],
        )
    
    
class TestLandGSectorHtml(unittest.TestCase):
    def test_sums_by_sector_and_maps_canonical(self) -> None:
        rows = LAndGPosition._sectors_from_portfolio_html(
            _sector_html().decode()
        )
        self.assertEqual(
            rows,
            [
                {"name": "Finance", "weight_pct": 44.9},
                {"name": "Real Estate", "weight_pct": 17.9},
                {"name": "Industrials", "weight_pct": 11.7},
                {"name": "Consumer", "weight_pct": 10.6},  # Consumer Discretionary + Consumer Staples
                {"name": "Healthcare", "weight_pct": 8.5},
                {"name": "Telecommunication", "weight_pct": 2.8},
                {"name": "Materials", "weight_pct": 1.6},
                {"name": "Utilities", "weight_pct": 1.6},
                {"name": "Technology", "weight_pct": 0.4},
            ],
        )

    def test_maps_cash_to_other(self) -> None:
        rows = LAndGPosition._sectors_from_portfolio_html(
            _sector_html([["Cash", "0.5"], ["Information Technology", "99.5"]]).decode()
        )
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 99.5},
                {"name": "Other", "weight_pct": 0.5},
            ],
        )

    def test_skips_empty_sector_tables(self) -> None:
        self.assertEqual(
            LAndGPosition._sectors_from_portfolio_html("<html></html>"),
            [],
        )


class TestLandGProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _LANDG_PRODUCT_EXISTS.clear()
        _LANDG_SHARECLASS.clear()

    def tearDown(self) -> None:
        _LANDG_PRODUCT_EXISTS.clear()
        _LANDG_SHARECLASS.clear()

    def test_exists_on_country_canvas(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_listing_then_part(_LISTING, _portfolio_html()),
        ) as opener:
            self.assertTrue(landg_product_url_exists(_ISIN))
        self.assertGreaterEqual(opener.call_count, 2)

    def test_missing_country_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_listing_then_part(_LISTING, b"<html></html>"),
        ):
            self.assertFalse(landg_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(landg_product_url_exists(""))
        opener.assert_not_called()

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://fundcentres.landg.com/", 404),
        ):
            self.assertFalse(landg_product_url_exists(_ISIN))

    def test_timeout_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            self.assertFalse(landg_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_urlopen_listing_then_part(_LISTING, _portfolio_html()),
        ) as opener:
            self.assertTrue(landg_product_url_exists(_ISIN))
            self.assertTrue(landg_product_url_exists(_ISIN))
        self.assertGreaterEqual(opener.call_count, 2)
        self.assertEqual(opener.call_count, 2)


class TestLandGFactoryRouting(unittest.TestCase):
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
            "name": "L&G Asia Pacific ex Japan ESG Paris Aligned UCITS ETF",
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

    def test_allowlisted_isin_and_existing_url_use_landg(self) -> None:
        with patch("position.factory.landg_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, LAndGPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.landg_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="Asia Pacific ex Japan ESG Paris Aligned")
        self.assertIsInstance(pos, LAndGPosition)

    def test_lg_in_name_without_allowlist_still_probes(self) -> None:
        with patch("position.factory.landg_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(
                    isin="IE00B3CNHJ55",
                    name="L&G Russell 2000 US Small Cap UCITS ETF",
                )
        self.assertIsInstance(pos, LAndGPosition)

    def test_missing_product_falls_back_to_justetf(self) -> None:
        with patch("position.factory.landg_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, LAndGPosition)

    def test_amundi_does_not_use_landg(self) -> None:
        with patch("position.factory.landg_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, LAndGPosition)

    def test_without_fetch_geosplit_skips_landg_probe(self) -> None:
        self.ctx.config.fetch_geosplit = False
        with patch("position.factory.landg_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, LAndGPosition)


class TestLandGSectorFetch(unittest.TestCase):
    def setUp(self) -> None:
        _LANDG_SHARECLASS.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_sectorsplit=True,
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
            "name": "L&G Asia Pacific ex Japan ESG Paris Aligned UCITS ETF",
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

    def _mock_sector_http_get(self, listing: dict, part_html: bytes):
        """Return a mock for urllib.request.urlopen that handles listing, country, and sector requests."""
        listing_body = json.dumps(listing).encode()
        country_html = _portfolio_html()
        sector_html = part_html

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "fund-centre/" in url and "part?" not in url:
                resp = MagicMock()
                resp.status = 200
                resp.headers = {"Content-Type": "application/json"}
                resp.read.return_value = listing_body
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = False
                return resp
            # The L&G part URLs use `id=` parameter, not `part_id=`
            if f"id={_LANDG_PORTFOLIO_PART_ID}" in url:
                resp = MagicMock()
                resp.status = 200
                resp.headers = {"Content-Type": "text/html"}
                resp.read.return_value = country_html
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = False
                return resp
            if f"id={_LANDG_SECTOR_PART_ID}" in url:
                resp = MagicMock()
                resp.status = 200
                resp.headers = {"Content-Type": "text/html"}
                resp.read.return_value = sector_html
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = False
                return resp
            resp = MagicMock()
            resp.status = 200
            resp.headers = {"Content-Type": "text/html"}
            resp.read.return_value = b"<html></html>"
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            return resp

        return mock_urlopen

    def test_parses_sector_html(self) -> None:
        with patch(
            "position.l_and_g_position.urllib.request.urlopen",
            side_effect=self._mock_sector_http_get(_LISTING, _sector_html()),
        ):
            with patch.object(LAndGPosition, "_fast_info_price", return_value=12.0):
                pos = LAndGPosition(
                    _ISIN, name="L&G Asia Pacific ex Japan ESG Paris Aligned", shares=1, ctx=self.ctx
                )
        self.assertEqual(
            pos.sectors(),
            [
                {"name": "Finance", "weight_pct": 44.9},
                {"name": "Real Estate", "weight_pct": 17.9},
                {"name": "Industrials", "weight_pct": 11.7},
                {"name": "Consumer", "weight_pct": 10.6},
                {"name": "Healthcare", "weight_pct": 8.5},
                {"name": "Telecommunication", "weight_pct": 2.8},
                {"name": "Materials", "weight_pct": 1.6},
                {"name": "Utilities", "weight_pct": 1.6},
                {"name": "Technology", "weight_pct": 0.4},
            ],
)
        
    def test_sector_fallback_to_justetf(self) -> None:
        with patch(
            "position.l_and_g_position.urllib.request.urlopen",
            side_effect=self._mock_sector_http_get(_LISTING, b"<html></html>"),
        ):
            with patch.object(LAndGPosition, "_fast_info_price", return_value=12.0):
                with patch.object(JustETFPosition, "_fetch_sectors_with_retries", return_value=[]):
                    pos = LAndGPosition(
                        _ISIN, name="L&G Asia Pacific ex Japan ESG Paris Aligned", shares=1, ctx=self.ctx
                    )
        # Falls back to JustETF sector scrape (which returns empty in mock)
        self.assertEqual(pos.sectors(), [])

    def test_without_fetch_sectorsplit_skips_sector_scrape(self) -> None:
        self.ctx.config.fetch_sectorsplit = False
        with patch("position.factory.landg_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        # The position is created but sector scrape is skipped
        self.assertEqual(pos.sectors(), [])


if __name__ == "__main__":
    unittest.main()
