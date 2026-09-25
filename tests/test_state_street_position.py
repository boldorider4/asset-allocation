# SPDX-License-Identifier: AGPL-3.0-or-later
"""SSGA geographical JSON country aggregation and factory routing."""

from __future__ import annotations

import html
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
from position.state_street_position import (
    StateStreetPosition,
    _SSGA_PRODUCT_EXISTS,
    ssga_product_url_exists,
)
from cli.context import AppConfig, RuntimeContext

_ISIN = "IE00B4YBJ215"
_GEO = {
    "label": "Fund Geographical Weights",
    "asOfDate": "as of 11 Sep 2026",
    "attrArray": [
        {
            "name": {"label": "Name", "value": "United States"},
            "weight": {
                "label": "Weight",
                "value": "99,56%",
                "originalValue": "99.558113",
            },
        },
        {
            "name": {"label": "Name", "value": "Canada"},
            "weight": {
                "label": "Weight",
                "value": "0,44%",
                "originalValue": "0.441887",
            },
        },
        {
            "name": {"label": "Name", "value": "Korea, Republic of"},
            "weight": {"label": "Weight", "value": "0,10%", "originalValue": "0.1"},
        },
        {
            "name": {"label": "Name", "value": "Cash"},
            "weight": {"label": "Weight", "value": "0,01%", "originalValue": "0.01"},
        },
        {
            "name": {"label": "Name", "value": "UAE"},
            "weight": {"label": "Weight", "value": "0,00%", "originalValue": "0"},
        },
    ],
}

_SECTOR = {
    "label": "Fund Sector Allocation",
    "asOfDate": "as of 18 Sep 2026",
    "attrArray": [
        {"name": {"label": "Sector", "value": "Industrials"}, "weight": {"label": "Weight", "value": "23,86%", "originalValue": "23.858768"}},
        {"name": {"label": "Sector", "value": "Financials"}, "weight": {"label": "Weight", "value": "15,37%", "originalValue": "15.368496"}},
        {"name": {"label": "Sector", "value": "Information Technology"}, "weight": {"label": "Weight", "value": "14,87%", "originalValue": "14.873416"}},
        {"name": {"label": "Sector", "value": "Health Care"}, "weight": {"label": "Weight", "value": "10,14%", "originalValue": "10.138749"}},
        {"name": {"label": "Sector", "value": "Consumer Discretionary"}, "weight": {"label": "Weight", "value": "9,82%", "originalValue": "9.815022"}},
        {"name": {"label": "Sector", "value": "Real Estate"}, "weight": {"label": "Weight", "value": "7,09%", "originalValue": "7.087757"}},
        {"name": {"label": "Sector", "value": "Materials"}, "weight": {"label": "Weight", "value": "5,84%", "originalValue": "5.844839"}},
        {"name": {"label": "Sector", "value": "Energy"}, "weight": {"label": "Weight", "value": "5,26%", "originalValue": "5.259488"}},
        {"name": {"label": "Sector", "value": "Consumer Staples"}, "weight": {"label": "Weight", "value": "3,16%", "originalValue": "3.155771"}},
        {"name": {"label": "Sector", "value": "Utilities"}, "weight": {"label": "Weight", "value": "3,07%", "originalValue": "3.066129"}},
        {"name": {"label": "Sector", "value": "Communication Services"}, "weight": {"label": "Weight", "value": "1,53%", "originalValue": "1.531566"}},
        {"name": {"label": "Sector", "value": "Cash"}, "weight": {"label": "Weight", "value": "0,01%", "originalValue": "0.01"}},
    ],
}


def _html_page(payload: dict) -> bytes:
    encoded = html.escape(json.dumps(payload), quote=True)
    return (
        "<html><body>"
        f'<input type="hidden" id="fund-geographical-breakdown" value="{encoded}"/>'
        f'<input type="hidden" id="fund-sector-breakdown" value="{encoded}"/>'
        "</body></html>"
    ).encode()


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _html_response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": "text/html;charset=utf-8"}
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestGeoJsonAggregation(unittest.TestCase):
    def test_sums_by_country_and_aliases(self) -> None:
        rows = StateStreetPosition._countries_from_geo_json(_GEO)
        self.assertEqual(
            rows,
            [
                {"name": "United States", "weight_pct": 99.558113},
                {"name": "Canada", "weight_pct": 0.441887},
                {"name": "South Korea", "weight_pct": 0.1},
                {"name": "Other", "weight_pct": 0.01},
            ],
        )

    def test_empty_or_invalid_payload(self) -> None:
        self.assertEqual(StateStreetPosition._countries_from_geo_json({}), [])
        self.assertIsNone(StateStreetPosition._geo_payload_from_html("<html></html>"))

    def test_parses_hidden_input_from_html(self) -> None:
        payload = StateStreetPosition._geo_payload_from_html(
            _html_page(_GEO).decode()
        )
        self.assertEqual(payload["label"], "Fund Geographical Weights")
        self.assertEqual(
            StateStreetPosition._countries_from_geo_json(payload)[0]["name"],
            "United States",
        )


class TestSectorJsonAggregation(unittest.TestCase):
    def test_sectors_from_geo_json(self) -> None:
        rows = StateStreetPosition._sectors_from_geo_json(_SECTOR)
        # Mapped to canonical: Information Technology->Technology, Financials->Finance,
        # Health Care->Healthcare, Consumer Discretionary->Consumer, Consumer Staples->Consumer,
        # Real Estate->Real Estate, Materials->Materials, Energy->Commodities, Consumer Staples->Consumer,
        # Utilities->Utilities, Communication Services->Telecommunication, Cash->Other
        self.assertEqual(
            rows,
            [
                {"name": "Industrials", "weight_pct": 23.858768},
                {"name": "Finance", "weight_pct": 15.368496},
                {"name": "Technology", "weight_pct": 14.873416},
                {"name": "Consumer", "weight_pct": 12.970793},  # Consumer Discretionary + Consumer Staples
                {"name": "Healthcare", "weight_pct": 10.138749},
                {"name": "Real Estate", "weight_pct": 7.087757},
                {"name": "Materials", "weight_pct": 5.844839},
                {"name": "Commodities", "weight_pct": 5.259488},
                {"name": "Utilities", "weight_pct": 3.066129},
                {"name": "Telecommunication", "weight_pct": 1.531566},
                {"name": "Other", "weight_pct": 0.01},
            ],
        )

    def test_empty_or_invalid_payload(self) -> None:
        self.assertEqual(StateStreetPosition._sectors_from_geo_json({}), [])
        self.assertIsNone(StateStreetPosition._sector_payload_from_html("<html></html>"))

    def test_parses_sector_hidden_input_from_html(self) -> None:
        payload = StateStreetPosition._sector_payload_from_html(
            _html_page(_SECTOR).decode()
        )
        self.assertEqual(payload["label"], "Fund Sector Allocation")
        self.assertEqual(
            StateStreetPosition._sectors_from_geo_json(payload)[0]["name"],
            "Industrials",
        )


class TestSsgaProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _SSGA_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _SSGA_PRODUCT_EXISTS.clear()

    _SLUG = "state-street-spdr-sp-400-us-mid-cap-ucits-etf-acc-spy4-gy"

    def test_exists_on_geo_html(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_GEO)),
        ) as opener:
            self.assertTrue(ssga_product_url_exists(_ISIN, self._SLUG))
        self.assertEqual(opener.call_count, 1)

    def test_missing_geo_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(b"<html></html>"),
        ):
            self.assertFalse(ssga_product_url_exists(_ISIN, self._SLUG))

    def test_unknown_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ssga_product_url_exists("IE00XXXXXXX1", None))
        opener.assert_not_called()

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ssga_product_url_exists("", None))
        opener.assert_not_called()

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://www.ssga.com/", 404),
        ):
            self.assertFalse(ssga_product_url_exists(_ISIN, self._SLUG))

    def test_timeout_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            self.assertFalse(ssga_product_url_exists(_ISIN, self._SLUG))

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_GEO)),
        ) as opener:
            self.assertTrue(ssga_product_url_exists(_ISIN, self._SLUG))
            self.assertTrue(ssga_product_url_exists(_ISIN, self._SLUG))
        self.assertEqual(opener.call_count, 1)


class TestSsgaCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_sectorsplit=True,
                fetch_prices=False,
                cache_file=tmp / "cache.json",
                isin_file=tmp / "isin.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True

    def test_parses_geo_html(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_GEO)),
        ):
            with patch.object(
                StateStreetPosition, "_fast_info_price", return_value=12.0
            ):
                pos = StateStreetPosition(
                    _ISIN,
                    name="State Street SPDR S&P 400 U.S. Mid Cap",
                    shares=1, ctx=self.ctx
                )
        # Fixture rows sum past 100: normalized to 100 at assembly.
        self.assertEqual(
            pos.countries(),
            [
                {"name": "United States", "weight_pct": 99.44871940865049},
                {"name": "Canada", "weight_pct": 0.4414014583957646},
                {"name": "South Korea", "weight_pct": 0.09989012086704625},
                {"name": "Other", "weight_pct": 0.009989012086704625},
            ],
        )

    def test_parses_sector_html(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_SECTOR)),
        ):
            with patch.object(
                StateStreetPosition, "_fast_info_price", return_value=12.0
            ):
                pos = StateStreetPosition(
                    _ISIN, name="State Street SPDR S&P 400 U.S. Mid Cap", shares=1, ctx=self.ctx
                )
        # Fixture rows sum past 100: normalized to 100 at assembly.
        self.assertEqual(
            pos.sectors(),
            [
                {"name": "Industrials", "weight_pct": 23.856382123223856},
                {"name": "Finance", "weight_pct": 15.366959150415367},
                {"name": "Technology", "weight_pct": 14.871928658414872},
                {"name": "Consumer", "weight_pct": 12.96949592071297},
                {"name": "Healthcare", "weight_pct": 10.137735125110138},
                {"name": "Real Estate", "weight_pct": 7.087048224307087},
                {"name": "Materials", "weight_pct": 5.844254516105845},
                {"name": "Commodities", "weight_pct": 5.258962051205259},
                {"name": "Utilities", "weight_pct": 3.065822387103066},
                {"name": "Telecommunication", "weight_pct": 1.5314128434015313},
                {"name": "Other", "weight_pct": 0.009999000000009998},
            ],
        )


class TestSsgaFactoryRouting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)
        self.ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=True,
                fetch_prices=False,
                cache_file=tmp / "cache.json",
                isin_file=tmp / "isin.json",
                assets_file=tmp / "assets.json",
            )
        )
        self.ctx.cache = {}
        self.ctx.cache_loaded = True

    def _factory(self, **kwargs):
        defaults = {
            "isin": _ISIN,
            "name": "State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF (Acc)",
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

    def test_allowlisted_isin_and_existing_url_use_ssga(self) -> None:
        with patch("position.factory.ssga_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, StateStreetPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.ssga_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="S&P 400 US Mid Cap UCITS ETF")
        self.assertIsInstance(pos, StateStreetPosition)

    def test_spdr_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.ssga_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU0290358497",
                    name="SPDR MSCI World UCITS ETF",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, StateStreetPosition)

    def test_missing_product_falls_back_to_justetf(self) -> None:
        # Seeded rows take the DB path, so a probe miss can only be
        # exercised with an unseeded ISIN and a hermetic probe helper.
        with patch("position.factory._probe_issuer_for_isin", return_value=None):
            with self._no_country_scrape():
                pos = self._factory(isin="XX00040402")
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, StateStreetPosition)

    def test_amundi_does_not_use_ssga(self) -> None:
        with patch("position.factory.ssga_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, StateStreetPosition)

    def test_without_fetch_geosplit_skips_ssga_probe(self) -> None:
        self.ctx.config.fetch_geosplit = False
        with patch("position.factory.ssga_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, StateStreetPosition)


if __name__ == "__main__":
    unittest.main()
