"""UBS constituents XLSX country aggregation and factory routing."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.sax.saxutils import escape

from position.amundi_position import AmundiPosition
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.ubs_position import (
    UBSPosition,
    _UBS_PRODUCT_EXISTS,
    _UBS_TOKEN_URL,
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


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    cells = []
    for r_i, record in enumerate(rows, start=1):
        parts = []
        for c_i, value in enumerate(record):
            col = chr(ord("A") + c_i)
            parts.append(
                f'<c r="{col}{r_i}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            )
        cells.append(f'<row r="{r_i}">{"".join(parts)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


_HOLDINGS_XLSX = _xlsx_bytes(
    [
        ["UBS Core MSCI World UCITS ETF USD acc"],
        ["ISIN: : IE00BD4TXV59"],
        [],
        [],
        ["Securities", "ISIN", "Sedol Code", "Currency", "Price", "Weight %"],
        ["TSMC", "TW0002330008", "6889106", "TWD", "1", "15.00"],
        ["FOO", "TW0000050004", "6226321", "TWD", "1", "13.04"],
        ["SAMSUNG", "KR7005930003", "6771720", "KRW", "1", "21.77"],
        ["APPLE", "US0378331005", "2046251", "USD", "1", "2.00"],
        ["SKIP ZERO", "US5949181045", "2588173", "USD", "1", "0.00"],
        ["NO ISIN", "", "", "USD", "1", "0.01"],
        ["Source: State Street, 10.09.2026"],
    ]
)


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


def _xlsx_response(body: bytes = _HOLDINGS_XLSX) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.headers = {
        "Content-Type": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    }
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestHoldingsXlsxAggregation(unittest.TestCase):
    def test_sums_by_isin_country_prefix(self) -> None:
        rows = UBSPosition._countries_from_holdings_xlsx(_HOLDINGS_XLSX)
        self.assertEqual(
            rows,
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.0},
            ],
        )

    def test_empty_or_invalid_xlsx(self) -> None:
        self.assertEqual(UBSPosition._countries_from_holdings_xlsx(b""), [])
        self.assertEqual(UBSPosition._countries_from_holdings_xlsx(b"not xlsx"), [])

    def test_maps_special_and_unknown_isin_prefixes_to_other(self) -> None:
        payload = _xlsx_bytes(
            [
                ["Securities", "ISIN", "Weight %"],
                ["EUROBOND", "XS1234567890", "1.5"],
                ["CINS", "XA0987654321", "0.4"],
                ["UNKNOWN", "ZZ0000000001", "0.1"],
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_holdings_xlsx(payload),
            [{"name": "Other", "weight_pct": 2.0}],
        )

    def test_maps_country_prefixes_including_liberia(self) -> None:
        payload = _xlsx_bytes(
            [
                ["Securities", "ISIN", "Weight %"],
                ["IE NAME", "IE00BD4TXV59", "2.0"],
                ["IT NAME", "IT0001234567", "1.0"],
                ["GB NAME", "GB00B16GWD56", "3.0"],
                ["LR NAME", "LR0000000000", "0.5"],
            ]
        )
        self.assertEqual(
            UBSPosition._countries_from_holdings_xlsx(payload),
            [
                {"name": "United Kingdom", "weight_pct": 3.0},
                {"name": "Ireland", "weight_pct": 2.0},
                {"name": "Italy", "weight_pct": 1.0},
                {"name": "Liberia", "weight_pct": 0.5},
            ],
        )

    def test_normalizes_pycountry_names_to_market_lists(self) -> None:
        self.assertEqual(UBSPosition._country_from_isin("KR7005930003"), "South Korea")
        self.assertEqual(UBSPosition._country_from_isin("TW0002330008"), "Taiwan")
        self.assertEqual(UBSPosition._country_from_isin("US0378331005"), "United States")
        self.assertEqual(UBSPosition._country_from_isin("MO0000000001"), "Macau")
        self.assertEqual(
            UBSPosition._country_from_isin("RU0000000001"), "Russian Federation"
        )
        self.assertEqual(UBSPosition._country_from_isin("EU0000000001"), "European Union")

    def test_maps_historic_prefix_via_pycountry(self) -> None:
        self.assertEqual(
            UBSPosition._country_from_isin("AN0000000001"),
            "Netherlands Antilles",
        )


class TestUbsProductExists(unittest.TestCase):
    def setUp(self) -> None:
        _UBS_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _UBS_PRODUCT_EXISTS.clear()

    def test_exists_on_inst_id(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _json_response({"token": "t"}),
                _json_response({"instId": _INST_ID}),
            ],
        ) as opener:
            self.assertTrue(ubs_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 2)

    def test_missing_inst_id_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _json_response({"token": "t"}),
                _json_response({}),
            ],
        ):
            self.assertFalse(ubs_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ubs_product_url_exists(""))
        opener.assert_not_called()

    def test_http_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(_UBS_TOKEN_URL, 401),
        ):
            self.assertFalse(ubs_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _json_response({"token": "t"}),
                _json_response({"instId": _INST_ID}),
            ],
        ) as opener:
            self.assertTrue(ubs_product_url_exists(_ISIN))
            self.assertTrue(ubs_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 2)


class TestUbsCountryFetch(unittest.TestCase):
    def setUp(self) -> None:
        self._geo = get_fetch_geosplit()
        self._prices = get_fetch_prices()
        set_fetch_geosplit(True)
        set_fetch_prices(False)

    def tearDown(self) -> None:
        set_fetch_geosplit(self._geo)
        set_fetch_prices(self._prices)

    def test_aggregates_constituents_xlsx(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _json_response({"token": "t"}),
                _json_response({"instId": _INST_ID}),
                _xlsx_response(),
            ],
        ):
            with patch.object(UBSPosition, "_fast_info_price", return_value=12.0):
                pos = UBSPosition(_ISIN, name="UBS Core MSCI World", shares=1)
        self.assertEqual(
            pos.countries(),
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.0},
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

    def test_allowlisted_isin_and_existing_url_use_ubs(self) -> None:
        with patch("position.factory.ubs_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, UBSPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.ubs_product_url_exists", return_value=True):
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

    def test_missing_product_falls_back_to_justetf(self) -> None:
        with patch("position.factory.ubs_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
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
