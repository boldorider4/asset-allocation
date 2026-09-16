"""JustETF sector scrape: parsing, failure fallback, and cache behavior."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from common import PENDING_FETCHED_VALUES
from position.factory import factory
from position.justetf_position import JustETFPosition
from position.position import Position
from position.yfinance_position import YFinancePosition
from utils import (
    get_fetch_geosplit,
    get_fetch_prices,
    get_fetch_sectorsplit,
    parse_cache_entry,
    save_position_in_cache,
    sectors_to_cache_fractions,
    set_fetch_geosplit,
    set_fetch_prices,
    set_fetch_sectorsplit,
    portfolio as global_portfolio,
)

_ISIN = "IE00BTJRMP35"
_HTTP_403 = urllib.error.HTTPError(
    "https://www.justetf.com", 403, "Forbidden", {}, None
)

_SECTOR_HTML = """
<table data-testid="etf-holdings_sectors_table"><tbody>
<tr><td data-testid="tl_etf-holdings_sectors_value_name">Technology</td>
<td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">43.04%</span></div></td></tr>
<tr><td data-testid="tl_etf-holdings_sectors_value_name">Finance</td>
<td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">22.38%</span></div></td></tr>
</tbody></table>
"""


class TestSectorTableParsing(unittest.TestCase):
    def _pos(self) -> JustETFPosition:
        geo, sec = get_fetch_geosplit(), get_fetch_sectorsplit()
        set_fetch_geosplit(False)
        set_fetch_sectorsplit(False)
        try:
            return JustETFPosition(_ISIN, name="EM ETF", price=12.0)
        finally:
            set_fetch_geosplit(geo)
            set_fetch_sectorsplit(sec)

    def test_parses_sector_rows(self) -> None:
        rows = self._pos()._sectors_from_html_table(_SECTOR_HTML)
        self.assertEqual(
            rows,
            [
                {"name": "Technology", "weight_pct": 43.04},
                {"name": "Finance", "weight_pct": 22.38},
            ],
        )

    def test_empty_html_returns_empty(self) -> None:
        pos = self._pos()
        self.assertEqual(pos._sectors_from_html_table(""), [])
        self.assertEqual(
            pos._sectors_from_html_table("<html></html>"), []
        )

    def test_aggregates_raw_labels_to_canonical(self) -> None:
        html = """
        <table data-testid="etf-holdings_sectors_table"><tbody>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Technology</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">43.04%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Financials</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">5.00%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Consumer Cyclicals</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">3.92%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Consumer Non-Cyclicals</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">6.21%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Communication Services</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">2.00%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Telecommunication</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">1.00%</span></div></td></tr>
        </tbody></table>
        """
        rows = self._pos()._sectors_from_html_table(html)
        self.assertEqual(
            [r["name"] for r in rows],
            ["Technology", "Consumer", "Finance", "Telecommunication"],
        )
        by_name = {str(r["name"]): float(r["weight_pct"]) for r in rows}
        self.assertAlmostEqual(by_name["Technology"], 43.04)
        self.assertAlmostEqual(by_name["Consumer"], 3.92 + 6.21)
        self.assertAlmostEqual(by_name["Finance"], 5.00)
        self.assertAlmostEqual(by_name["Telecommunication"], 3.00)

    def test_unknown_sector_folds_into_other(self) -> None:
        self.assertEqual(
            JustETFPosition._canonical_sector_name("Unobtanium"), "Other"
        )
        self.assertEqual(
            JustETFPosition._canonical_sector_name("  Finance  "), "Finance"
        )

    def test_government_family_maps_to_government(self) -> None:
        for raw in ("Sovereign", "Non-Corporate", "Government Agencies", "Municipal"):
            self.assertEqual(
                JustETFPosition._canonical_sector_name(raw), "Government"
            )

    def test_html_table_folds_unknown_labels(self) -> None:
        html = """
        <table data-testid="etf-holdings_sectors_table"><tbody>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Unobtanium</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">99.81%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Other</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">0.19%</span></div></td></tr>
        </tbody></table>
        """
        rows = self._pos()._sectors_from_html_table(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Other")
        self.assertAlmostEqual(float(rows[0]["weight_pct"]), 100.0)

    def test_html_table_aggregates_government_family(self) -> None:
        html = """
        <table data-testid="etf-holdings_sectors_table"><tbody>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Sovereign</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">80.00%</span></div></td></tr>
        <tr><td data-testid="tl_etf-holdings_sectors_value_name">Non-Corporate</td>
        <td><div><span data-testid="tl_etf-holdings_sectors_value_percentage">20.00%</span></div></td></tr>
        </tbody></table>
        """
        rows = self._pos()._sectors_from_html_table(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Government")
        self.assertAlmostEqual(float(rows[0]["weight_pct"]), 100.0)


class TestCachedSectorRows(unittest.TestCase):
    def test_fractions_scale_to_weight_pct(self) -> None:
        self.assertEqual(
            Position._cached_sectors_to_rows({"Technology": 0.4304}),
            [{"name": "Technology", "weight_pct": 43.04}],
        )

    def test_unknown_labels_fold_into_other_summing_duplicates(self) -> None:
        rows = Position._cached_sectors_to_rows(
            {"Sovereign": 0.9981, "Other": 0.0019}
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Other")
        self.assertAlmostEqual(float(rows[0]["weight_pct"]), 100.0)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(Position._cached_sectors_to_rows(None))


class TestJustETFSectorScrapeFailure(unittest.TestCase):
    def setUp(self) -> None:
        self._sec = get_fetch_sectorsplit()
        set_fetch_sectorsplit(True)

    def tearDown(self) -> None:
        set_fetch_sectorsplit(self._sec)

    def _position(self, error: Exception) -> JustETFPosition:
        with patch.object(
            JustETFPosition, "_fetch_sectors_with_retries", side_effect=error
        ):
            with patch.object(JustETFPosition, "_fast_info_price", return_value=12.0):
                return JustETFPosition(_ISIN, name="EM ETF", shares=10)

    def test_http_error_returns_empty_sectors(self) -> None:
        self.assertEqual(self._position(_HTTP_403).sectors(), [])

    def test_runtime_error_returns_empty_sectors(self) -> None:
        pos = self._position(RuntimeError("JustETF HTTP 403"))
        self.assertEqual(pos.sectors(), [])

    def test_failure_logs_warning(self) -> None:
        with self.assertLogs("position.justetf_position", level="WARNING") as logs:
            self._position(_HTTP_403)
        self.assertIn(_ISIN, "\n".join(logs.output))


class TestSectorCacheUtils(unittest.TestCase):
    def test_parse_cache_entry_returns_sectors(self) -> None:
        price, countries, sectors = parse_cache_entry(
            {"price": 10.0, "countries": {"Germany": 0.4}, "sectors": {"Technology": 0.43}}
        )
        self.assertEqual(price, 10.0)
        self.assertEqual(countries, {"Germany": 0.4})
        self.assertEqual(sectors, {"Technology": 0.43})

    def test_parse_cache_entry_without_sectors(self) -> None:
        price, countries, sectors = parse_cache_entry({"price": 10.0})
        self.assertEqual(price, 10.0)
        self.assertIsNone(countries)
        self.assertIsNone(sectors)

    def test_sectors_to_cache_fractions(self) -> None:
        self.assertEqual(
            sectors_to_cache_fractions(
                [{"name": "Technology", "weight_pct": 43.04}]
            ),
            {"Technology": 0.4304},
        )
        self.assertEqual(sectors_to_cache_fractions(None), {})
        self.assertEqual(sectors_to_cache_fractions([]), {})

    def test_save_position_in_cache_sectors(self) -> None:
        cache: dict = {}
        save_position_in_cache(
            cache,
            _ISIN,
            sectors=[{"name": "Technology", "weight_pct": 43.04}],
            update_sectors=True,
        )
        self.assertEqual(cache[_ISIN]["sectors"], {"Technology": 0.4304})


class TestFactorySectorFlags(unittest.TestCase):
    def setUp(self) -> None:
        self._prices = get_fetch_prices()
        self._geo = get_fetch_geosplit()
        self._sec = get_fetch_sectorsplit()
        self._saved_portfolio = copy.deepcopy(dict(global_portfolio))
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache = Path(self._tmpdir.name) / "cache.json"
        self._cache.write_text(
            json.dumps(
                {
                    "IE00BTJRMP35": {
                        "price": 10.0,
                        "countries": {"Taiwan": 0.2654},
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        set_fetch_prices(self._prices)
        set_fetch_geosplit(self._geo)
        set_fetch_sectorsplit(self._sec)
        global_portfolio.clear()
        global_portfolio.update(copy.deepcopy(self._saved_portfolio))
        PENDING_FETCHED_VALUES.clear()
        self._tmpdir.cleanup()

    def _factory(self, **kwargs):
        defaults = {
            "isin": "IE00BTJRMP35",
            "name": "Xtrackers EM",
            "shares": 4,
            "value": None,
            "broker": "other",
            "price": None,
        }
        defaults.update(kwargs)
        with patch("utils.CACHE_FILENAME", str(self._cache)):
            return factory(**defaults)

    def test_fetch_sectorsplit_scrapes_and_writes_sectors(self) -> None:
        set_fetch_sectorsplit(True)
        set_fetch_prices(False)
        set_fetch_geosplit(False)
        sample = [{"name": "Technology", "weight_pct": 43.04}]
        with patch.object(
            JustETFPosition, "_fetch_sectors_with_retries", return_value=sample
        ) as mocked:
            with patch.object(
                JustETFPosition,
                "_fast_info_price",
                side_effect=AssertionError("must not scrape price"),
            ):
                pos = self._factory()
        mocked.assert_called()
        self.assertEqual(pos.sectors(), sample)
        saved = json.loads(self._cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE00BTJRMP35"]["sectors"], {"Technology": 0.4304})
        self.assertEqual(saved["IE00BTJRMP35"]["price"], 10.0)

    def test_without_sectorsplit_missing_sectors_does_not_scrape(self) -> None:
        set_fetch_sectorsplit(False)
        set_fetch_geosplit(False)
        with patch.object(
            JustETFPosition,
            "_fetch_sectors_with_retries",
            side_effect=AssertionError("must not scrape sectors"),
        ):
            pos = self._factory()
        self.assertEqual(pos.sectors(), [])

    def test_without_sectorsplit_uses_cached_sectors(self) -> None:
        self._cache.write_text(
            json.dumps(
                {
                    "IE00BTJRMP35": {
                        "price": 10.0,
                        "sectors": {"Technology": 0.4304},
                    }
                }
            ),
            encoding="utf-8",
        )
        set_fetch_sectorsplit(False)
        set_fetch_geosplit(False)
        with patch.object(
            JustETFPosition,
            "_fetch_sectors_with_retries",
            side_effect=AssertionError("must not scrape sectors"),
        ):
            pos = self._factory()
        self.assertEqual(
            pos.sectors(), [{"name": "Technology", "weight_pct": 43.04}]
        )

    def test_yfinance_sectors_raises_not_implemented(self) -> None:
        set_fetch_sectorsplit(False)
        set_fetch_geosplit(False)
        with patch.object(YFinancePosition, "_fast_info_price", return_value=12.0):
            pos = YFinancePosition(_ISIN, name="EM ETF", shares=10)
        with self.assertRaises(NotImplementedError):
            pos.sectors()


if __name__ == "__main__":
    unittest.main()
