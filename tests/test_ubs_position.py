"""UBS HA4 constituents country aggregation and factory routing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from position.amundi_position import AmundiPosition
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.ubs_position import (
    UBSPosition,
    _UBS_PRODUCT_EXISTS,
    ubs_product_url_exists,
)
from utils import (
    get_fetch_geosplit,
    get_fetch_prices,
    set_fetch_geosplit,
    set_fetch_prices,
)

_ISIN = "IE00BD4TXV59"
_INST_ID = "1694907"


def _gql_payload(rows: list[dict[str, str]]) -> dict:
    graphql_rows: list[dict] = [
        {
            "type": "header",
            "cell": [
                {"id": "P_ISIN", "data": "ISIN"},
                {"id": "Currency", "data": "Currency"},
                {"id": "Weight", "data": "Weight %"},
            ],
        }
    ]
    for row in rows:
        graphql_rows.append(
            {
                "type": "data",
                "cell": [
                    {"id": "P_ISIN", "data": row.get("isin", "")},
                    {"id": "Currency", "data": row.get("currency", "")},
                    {"id": "Weight", "data": row.get("weight", "")},
                ],
            }
        )
    return {
        "data": {
            "getConstituentsExcel": {
                "etfFundHoldingsLargestConstituents": {"row": graphql_rows}
            }
        }
    }


_HOLDINGS = _gql_payload(
    [
        {"isin": "TW0002330008", "currency": "TWD", "weight": "15.00"},
        {"isin": "TW0000050004", "currency": "TWD", "weight": "13.04"},
        {"isin": "KR7005930003", "currency": "KRW", "weight": "21.77"},
        {"isin": "US0378331005", "currency": "USD", "weight": "2.00"},
        {"isin": "US5949181045", "currency": "USD", "weight": "0.00"},
        {"isin": "", "currency": "USD", "weight": "0.01"},
    ]
)


class TestHoldingsJsonAggregation(unittest.TestCase):
    def test_sums_by_listing_currency(self) -> None:
        rows = UBSPosition._countries_from_constituents_payload(_HOLDINGS)
        self.assertEqual(
            rows,
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.01},
            ],
        )

    def test_empty_or_invalid_payload(self) -> None:
        self.assertEqual(UBSPosition._countries_from_constituents_payload({}), [])
        self.assertEqual(
            UBSPosition._countries_from_constituents_payload({"data": {}}),
            [],
        )

    def test_usd_uses_currency_even_when_isin_prefix_differs(self) -> None:
        payload = _gql_payload(
            [
                {"isin": "IE00B4BNMY34", "currency": "USD", "weight": "2.0"},
                {"isin": "KYG123456789", "currency": "USD", "weight": "1.0"},
                {"isin": "LR0000000000", "currency": "USD", "weight": "0.5"},
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_constituents_payload(payload),
            [{"name": "United States", "weight_pct": 3.5}],
        )

    def test_eur_maps_to_european_union(self) -> None:
        payload = _gql_payload(
            [
                {"isin": "DE0007164600", "currency": "EUR", "weight": "3.0"},
                {"isin": "NL0010273215", "currency": "EUR", "weight": "2.0"},
                {"isin": "IT0003128367", "currency": "EUR", "weight": "1.0"},
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_constituents_payload(payload),
            [{"name": "European Union", "weight_pct": 6.0}],
        )

    def test_maps_special_and_unknown_isin_prefixes_to_other(self) -> None:
        payload = _gql_payload(
            [
                {"isin": "XS1234567890", "currency": "", "weight": "1.5"},
                {"isin": "XA0987654321", "currency": "", "weight": "0.4"},
                {"isin": "ZZ0000000001", "currency": "", "weight": "0.1"},
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_constituents_payload(payload),
            [{"name": "Other", "weight_pct": 2.0}],
        )

    def test_maps_country_prefixes_when_currency_column_missing(self) -> None:
        payload = _gql_payload(
            [
                {"isin": "IE00BD4TXV59", "currency": "", "weight": "2.0"},
                {"isin": "IT0001234567", "currency": "", "weight": "1.0"},
                {"isin": "GB00B16GWD56", "currency": "", "weight": "3.0"},
                {"isin": "LR0000000000", "currency": "", "weight": "0.5"},
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_constituents_payload(payload),
            [
                {"name": "United Kingdom", "weight_pct": 3.0},
                {"name": "Ireland", "weight_pct": 2.0},
                {"name": "Italy", "weight_pct": 1.0},
                {"name": "Liberia", "weight_pct": 0.5},
            ],
        )

    def test_normalizes_ccy_names_to_market_lists(self) -> None:
        self.assertEqual(UBSPosition._country_from_isin("KR7005930003"), "South Korea")
        self.assertEqual(UBSPosition._country_from_isin("TW0002330008"), "Taiwan")
        self.assertEqual(UBSPosition._country_from_isin("US0378331005"), "United States")
        self.assertEqual(UBSPosition._country_from_isin("MO0000000001"), "Macau")
        self.assertEqual(
            UBSPosition._country_from_isin("RU0000000001"), "Russian Federation"
        )
        self.assertEqual(UBSPosition._country_from_isin("EU0000000001"), "European Union")
        self.assertEqual(UBSPosition._country_from_currency("TWD"), "Taiwan")
        self.assertEqual(UBSPosition._country_from_currency("KRW"), "South Korea")
        self.assertEqual(UBSPosition._country_from_currency("USD"), "United States")
        self.assertEqual(UBSPosition._country_from_currency("CNY"), "China")
        self.assertEqual(UBSPosition._country_from_currency("CNH"), "China")
        self.assertEqual(UBSPosition._country_from_currency("EUR"), "European Union")

    def test_historic_prefix_without_ccy_country_is_other(self) -> None:
        self.assertEqual(UBSPosition._country_from_isin("AN0000000001"), "Other")


class TestUbsProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _UBS_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _UBS_PRODUCT_EXISTS.clear()

    def test_exists_on_inst_id(self) -> None:
        with patch("position.ubs_position._new_ha4_session", return_value=MagicMock()):
            with patch("position.ubs_position._seed_ubs_product_page"):
                with patch("position.ubs_position._http_token", return_value="t"):
                    with patch(
                        "position.ubs_position._http_inst_id", return_value=_INST_ID
                    ) as inst:
                        self.assertTrue(ubs_product_url_exists(_ISIN))
        inst.assert_called_once()

    def test_missing_inst_id_is_false(self) -> None:
        with patch("position.ubs_position._new_ha4_session", return_value=MagicMock()):
            with patch("position.ubs_position._seed_ubs_product_page"):
                with patch("position.ubs_position._http_token", return_value="t"):
                    with patch("position.ubs_position._http_inst_id", return_value=None):
                        self.assertFalse(ubs_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("position.ubs_position._new_ha4_session") as session:
            self.assertFalse(ubs_product_url_exists(""))
        session.assert_not_called()

    def test_http_error_is_false(self) -> None:
        from position.ubs_position import _Ha4HttpError

        with patch("position.ubs_position._new_ha4_session", return_value=MagicMock()):
            with patch("position.ubs_position._seed_ubs_product_page"):
                with patch(
                    "position.ubs_position._http_token",
                    side_effect=_Ha4HttpError(401, "token"),
                ):
                    self.assertFalse(ubs_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch("position.ubs_position._new_ha4_session", return_value=MagicMock()):
            with patch("position.ubs_position._seed_ubs_product_page"):
                with patch("position.ubs_position._http_token", return_value="t"):
                    with patch(
                        "position.ubs_position._http_inst_id", return_value=_INST_ID
                    ) as inst:
                        self.assertTrue(ubs_product_url_exists(_ISIN))
                        self.assertTrue(ubs_product_url_exists(_ISIN))
        self.assertEqual(inst.call_count, 1)


class TestUbsCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._geo = get_fetch_geosplit()
        self._prices = get_fetch_prices()
        set_fetch_geosplit(True)
        set_fetch_prices(False)

    def tearDown(self) -> None:
        set_fetch_geosplit(self._geo)
        set_fetch_prices(self._prices)

    def test_aggregates_constituents_json(self) -> None:
        with patch("position.ubs_position._new_ha4_session", return_value=MagicMock()):
            with patch("position.ubs_position._seed_ubs_product_page"):
                with patch("position.ubs_position._http_token", return_value="t"):
                    with patch(
                        "position.ubs_position._http_inst_id", return_value=_INST_ID
                    ):
                        with patch.object(
                            UBSPosition,
                            "_http_constituents_payload",
                            return_value=_HOLDINGS,
                        ):
                            with patch.object(
                                UBSPosition, "_fast_info_price", return_value=12.0
                            ):
                                pos = UBSPosition(
                                    _ISIN, name="UBS Core MSCI World", shares=1
                                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.01},
            ],
        )


class TestUbsFactoryRouting(unittest.TestCase):
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
            "name": "UBS Core MSCI World UCITS ETF USD acc",
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

    def test_allowlisted_isin_uses_ubs(self) -> None:
        with self._no_country_scrape():
            pos = self._factory()
        self.assertIsInstance(pos, UBSPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with self._no_country_scrape():
            pos = self._factory(name="Core MSCI World UCITS ETF")
        self.assertIsInstance(pos, UBSPosition)

    def test_ubs_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.ubs_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="LU0290358497",
                    name="UBS ETF MSCI EMU UCITS ETF",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, UBSPosition)

    def test_amundi_does_not_use_ubs(self) -> None:
        with patch("position.factory.ubs_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, UBSPosition)

    def test_without_fetch_geosplit_skips_ubs_probe(self) -> None:
        set_fetch_geosplit(False)
        with patch("position.factory.ubs_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, UBSPosition)


if __name__ == "__main__":
    unittest.main()
