"""Invesco dng-api country aggregation and factory routing."""

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
from position.invesco_position import (
    InvescoPosition,
    _INVESCO_PRODUCT_EXISTS,
    invesco_product_url_exists,
)
from position.justetf_position import JustETFPosition
from utils import (
    get_fetch_geosplit,
    get_fetch_prices,
    set_fetch_geosplit,
    set_fetch_prices,
)

_ISIN = "IE00BKS7L097"
_HOLDINGS_ISIN = "IE000PJL7R74"
_HOLDINGS = {
    "isin": _ISIN,
    "effectiveDate": "2026-07-31",
    "holdingWeights": [
        {"name": "UnitedStates", "value": 99.7},
        {"name": "Switzerland", "value": 0.2},
        {"name": "Netherlands", "value": 0.1},
        {"name": "SouthKorea", "value": 0.05},
        {"name": "Cash", "value": 0.01},
        {"name": "UnitedKingdom", "value": 0.0},
    ],
}
_CONSTITUENTS = {
    "effectiveDate": "2026-09-11",
    "holdings": [
        {"name": "TSMC", "isin": "TW0002330008", "weight": 17.8376},
        {"name": "SK HYNIX", "isin": "KR7000660001", "weight": 7.2009},
        {"name": "TENCENT", "isin": "KYG875721634", "weight": 1.1191},
        {"name": "EUROBOND", "isin": "XS1234567890", "weight": 0.4},
        {"name": "NO ISIN", "isin": "", "weight": 0.2},
    ],
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


class TestHoldingsJsonAggregation(unittest.TestCase):
    def test_splits_camelcase_names_and_maps_cash(self) -> None:
        rows = InvescoPosition._countries_from_holdings_json(_HOLDINGS, _ISIN)
        self.assertEqual(
            rows,
            [
                {"name": "United States", "weight_pct": 99.7},
                {"name": "Switzerland", "weight_pct": 0.2},
                {"name": "Netherlands", "weight_pct": 0.1},
                {"name": "South Korea", "weight_pct": 0.05},
                {"name": "Other", "weight_pct": 0.01},
            ],
        )

    def test_wrong_isin_or_empty_payload(self) -> None:
        self.assertEqual(
            InvescoPosition._countries_from_holdings_json(_HOLDINGS, "IE00XXXXXXX1"),
            [],
        )
        self.assertEqual(InvescoPosition._countries_from_holdings_json({}, _ISIN), [])

    def test_sums_constituents_by_isin_prefix(self) -> None:
        rows = InvescoPosition._countries_from_constituents_json(_CONSTITUENTS)
        self.assertEqual(
            rows,
            [
                {"name": "Taiwan", "weight_pct": 17.8376},
                {"name": "South Korea", "weight_pct": 7.2009},
                {"name": "Cayman Islands", "weight_pct": 1.1191},
                {"name": "Other", "weight_pct": 0.4},
            ],
        )

    def test_maps_isin_prefixes_via_pycountry(self) -> None:
        self.assertEqual(InvescoPosition._country_from_isin("KR7000660001"), "South Korea")
        self.assertEqual(InvescoPosition._country_from_isin("TW0002330008"), "Taiwan")
        self.assertEqual(InvescoPosition._country_from_isin("KYG875721634"), "Cayman Islands")
        self.assertEqual(InvescoPosition._country_from_isin("CNE100004272"), "China")
        self.assertEqual(InvescoPosition._country_from_isin("EU0000000001"), "European Union")
        self.assertEqual(InvescoPosition._country_from_isin("XS1234567890"), "Other")
        self.assertEqual(InvescoPosition._country_from_isin("AN0000000001"), "Netherlands Antilles")


class TestInvescoProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _INVESCO_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _INVESCO_PRODUCT_EXISTS.clear()

    def test_exists_on_matching_json(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(_HOLDINGS),
        ) as opener:
            self.assertTrue(invesco_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 1)

    def test_wrong_isin_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response({"isin": "IE00XXXXXXX1", "holdingWeights": []}),
        ):
            self.assertFalse(invesco_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(invesco_product_url_exists(""))
        opener.assert_not_called()

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://dng-api.invesco.com/", 404),
        ):
            self.assertFalse(invesco_product_url_exists(_ISIN))

    def test_exists_on_holdings_when_country_empty(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[_json_response({}), _json_response(_CONSTITUENTS)],
        ) as opener:
            self.assertTrue(invesco_product_url_exists(_HOLDINGS_ISIN))
        self.assertEqual(opener.call_count, 2)

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_json_response(_HOLDINGS),
        ) as opener:
            self.assertTrue(invesco_product_url_exists(_ISIN))
            self.assertTrue(invesco_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 1)


class TestInvescoCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._geo = get_fetch_geosplit()
        self._prices = get_fetch_prices()
        set_fetch_geosplit(True)
        set_fetch_prices(False)

    def tearDown(self) -> None:
        set_fetch_geosplit(self._geo)
        set_fetch_prices(self._prices)

    def test_parses_country_json(self) -> None:
        with patch("urllib.request.urlopen", return_value=_json_response(_HOLDINGS)):
            with patch.object(InvescoPosition, "_fast_info_price", return_value=12.0):
                pos = InvescoPosition(
                    _ISIN, name="Invesco S&P 500 Scored & Screened", shares=1
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "United States", "weight_pct": 99.7},
                {"name": "Switzerland", "weight_pct": 0.2},
                {"name": "Netherlands", "weight_pct": 0.1},
                {"name": "South Korea", "weight_pct": 0.05},
                {"name": "Other", "weight_pct": 0.01},
            ],
        )

    def test_falls_back_to_holdings_isins_when_country_empty(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _json_response({}),
                _json_response(_CONSTITUENTS),
            ],
        ):
            with patch.object(InvescoPosition, "_fast_info_price", return_value=12.0):
                pos = InvescoPosition(
                    _HOLDINGS_ISIN,
                    name="Invesco MSCI Emerging Markets ESG Climate Paris Aligned",
                    shares=1,
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "Taiwan", "weight_pct": 17.8376},
                {"name": "South Korea", "weight_pct": 7.2009},
                {"name": "Cayman Islands", "weight_pct": 1.1191},
                {"name": "Other", "weight_pct": 0.4},
            ],
        )


class TestInvescoFactoryRouting(unittest.TestCase):
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
            "name": "Invesco S&P 500 Scored & Screened UCITS ETF Acc",
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

    def test_allowlisted_isin_and_existing_url_use_invesco(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, InvescoPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="S&P 500 Scored Screened UCITS ETF")
        self.assertIsInstance(pos, InvescoPosition)

    def test_invesco_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=False) as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU0290358497",
                    name="Invesco MSCI World UCITS ETF",
                )
        exists.assert_called_once_with("LU0290358497")
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, InvescoPosition)

    def test_dng_api_invesco_name_uses_invesco_without_allowlist(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(
                    isin="IE000XXXXXXX1",
                    name="Invesco Some Other UCITS ETF",
                )
        self.assertIsInstance(pos, InvescoPosition)

    def test_allowlisted_holdings_fallback_isin_uses_invesco(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(
                    isin=_HOLDINGS_ISIN,
                    name="Invesco MSCI Emerging Markets ESG Climate Paris Aligned UCITS ETF Acc",
                )
        self.assertIsInstance(pos, InvescoPosition)

    def test_missing_product_falls_back_to_justetf(self) -> None:
        with patch("position.factory.invesco_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, InvescoPosition)

    def test_amundi_does_not_use_invesco(self) -> None:
        with patch("position.factory.invesco_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, InvescoPosition)

    def test_without_fetch_geosplit_skips_invesco_probe(self) -> None:
        set_fetch_geosplit(False)
        with patch("position.factory.invesco_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, InvescoPosition)


if __name__ == "__main__":
    unittest.main()
