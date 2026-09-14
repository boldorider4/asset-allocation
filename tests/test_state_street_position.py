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
from utils import (
    get_fetch_geosplit,
    get_fetch_prices,
    set_fetch_geosplit,
    set_fetch_prices,
)

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


def _html_page(payload: dict) -> bytes:
    encoded = html.escape(json.dumps(payload), quote=True)
    return (
        "<html><body>"
        f'<input type="hidden" id="fund-geographical-breakdown" value="{encoded}"/>'
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


class TestSsgaProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _SSGA_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _SSGA_PRODUCT_EXISTS.clear()

    def test_exists_on_geo_html(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_GEO)),
        ) as opener:
            self.assertTrue(ssga_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 1)

    def test_missing_geo_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(b"<html></html>"),
        ):
            self.assertFalse(ssga_product_url_exists(_ISIN))

    def test_unknown_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ssga_product_url_exists("IE00XXXXXXX1"))
        opener.assert_not_called()

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ssga_product_url_exists(""))
        opener.assert_not_called()

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://www.ssga.com/", 404),
        ):
            self.assertFalse(ssga_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_html_response(_html_page(_GEO)),
        ) as opener:
            self.assertTrue(ssga_product_url_exists(_ISIN))
            self.assertTrue(ssga_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 1)


class TestSsgaCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._geo = get_fetch_geosplit()
        self._prices = get_fetch_prices()
        set_fetch_geosplit(True)
        set_fetch_prices(False)

    def tearDown(self) -> None:
        set_fetch_geosplit(self._geo)
        set_fetch_prices(self._prices)

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
                    shares=1,
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "United States", "weight_pct": 99.558113},
                {"name": "Canada", "weight_pct": 0.441887},
                {"name": "South Korea", "weight_pct": 0.1},
                {"name": "Other", "weight_pct": 0.01},
            ],
        )


class TestSsgaFactoryRouting(unittest.TestCase):
    def setUp(self) -> None:
        self._prices = get_fetch_prices()
        self._geo = get_fetch_geosplit()
        set_fetch_prices(False)
        set_fetch_geosplit(True)
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache = Path(self._tmpdir.name) / "cache.json"
        self._cache.write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        set_fetch_prices(self._prices)
        set_fetch_geosplit(self._geo)
        self._tmpdir.cleanup()

    def _factory(self, **kwargs):
        defaults = {
            "isin": _ISIN,
            "name": "State Street SPDR S&P 400 U.S. Mid Cap UCITS ETF (Acc)",
            "shares": 1,
            "price": 10.0,
        }
        defaults.update(kwargs)
        with patch("utils.CACHE_FILENAME", str(self._cache)):
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
        with patch("position.factory.ssga_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
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
        set_fetch_geosplit(False)
        with patch("position.factory.ssga_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, StateStreetPosition)


if __name__ == "__main__":
    unittest.main()
