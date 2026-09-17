"""iShares holdings CSV country aggregation and BlackRock factory routing."""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from position.amundi_position import AmundiPosition
from position.blackrock_position import (
    BlackRockPosition,
    _ISHARES_PRODUCT_EXISTS,
    _ishares_holdings_url,
    ishares_product_url_exists,
)
from position.factory import factory
from position.justetf_position import JustETFPosition
from context import AppConfig, RuntimeContext

_ISIN = "IE00BKM4GZ66"
_HOLDINGS_CSV = """Fund Holdings as of,10/Sept/2026
\xa0
Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Location
2330,TSMC,Information Technology,Equity,1,15.00,Taiwan
0050,FOO,Information Technology,Equity,1,13.04,Taiwan
005930,SAMSUNG,Information Technology,Equity,1,21.77,Korea (South)
USD,USD CASH,Cash and/or Derivatives,Cash,1,2.00,United States
FUT,MSCI EM INDEX SEP,Cash and/or Derivatives,Futures,1,0.00,-
EMPTY,SKIP ME,Equity,Equity,1,0.01,
"""


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _csv_response(body: str = _HOLDINGS_CSV) -> MagicMock:
    resp = MagicMock()
    resp.status = 200
    resp.headers = {"Content-Type": "text/csv;charset=UTF-8"}
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestHoldingsCsvAggregation(unittest.TestCase):
    def test_sums_by_country_and_aliases(self) -> None:
        rows = BlackRockPosition._countries_from_holdings_csv(_HOLDINGS_CSV)
        self.assertEqual(
            rows,
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.0},
            ],
        )

    def test_empty_csv(self) -> None:
        self.assertEqual(BlackRockPosition._countries_from_holdings_csv(""), [])
        self.assertEqual(
            BlackRockPosition._countries_from_holdings_csv("not a holdings table"),
            [],
        )

    def test_parses_weight_with_percent_sign(self) -> None:
        payload = (
            "Ticker,Name,Weight (%),Location\n"
            "AAPL,Apple,10.5%,United States\n"
        )
        self.assertEqual(
            BlackRockPosition._countries_from_holdings_csv(payload),
            [{"name": "United States", "weight_pct": 10.5}],
        )

    def test_discards_zero_and_negative_weights(self) -> None:
        payload = (
            "Ticker,Name,Weight (%),Location\n"
            "AAPL,Apple,10.5,United States\n"
            "ZERO,Zero Corp,0,United States\n"
            "NEG,Short,-0.20,Japan\n"
            "TSM,TSMC,1.0,Taiwan\n"
        )
        self.assertEqual(
            BlackRockPosition._countries_from_holdings_csv(payload),
            [
                {"name": "United States", "weight_pct": 10.5},
                {"name": "Taiwan", "weight_pct": 1.0},
            ],
        )


class TestIsharesProductUrl(unittest.TestCase):
    def setUp(self) -> None:
        _ISHARES_PRODUCT_EXISTS.clear()

    def tearDown(self) -> None:
        _ISHARES_PRODUCT_EXISTS.clear()

    def test_holdings_url_for_known_isin(self) -> None:
        self.assertEqual(
            _ishares_holdings_url(_ISIN),
            "https://www.ishares.com/ch/individual/en/products/264659"
            "/fund/1495092304805.ajax?fileType=csv",
        )

    def test_unknown_isin_has_no_url(self) -> None:
        self.assertIsNone(_ishares_holdings_url("XX0000000000"))

    def test_exists_on_200_csv(self) -> None:
        with patch("urllib.request.urlopen", return_value=_csv_response()) as opener:
            self.assertTrue(ishares_product_url_exists(_ISIN))
        opener.assert_called_once()

    def test_html_200_is_not_csv(self) -> None:
        resp = _csv_response()
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        with patch("urllib.request.urlopen", return_value=resp):
            self.assertFalse(ishares_product_url_exists(_ISIN))

    def test_missing_product_is_404(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(
                "https://www.ishares.com/ch/individual/en/products/999/fund/"
                "1495092304805.ajax?fileType=csv",
                404,
            ),
        ):
            self.assertFalse(ishares_product_url_exists(_ISIN))

    def test_unknown_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(ishares_product_url_exists("XX0000000000"))
        opener.assert_not_called()

    def test_network_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertFalse(ishares_product_url_exists(_ISIN))

    def test_timeout_error_is_false(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("The read operation timed out"),
        ):
            self.assertFalse(ishares_product_url_exists(_ISIN))

    def test_result_is_memoized(self) -> None:
        with patch("urllib.request.urlopen", return_value=_csv_response()) as opener:
            self.assertTrue(ishares_product_url_exists(_ISIN))
            self.assertTrue(ishares_product_url_exists(_ISIN))
        opener.assert_called_once()


class TestBlackRockCountryFetch(unittest.TestCase):
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

    def test_aggregates_holdings_csv(self) -> None:
        with patch("urllib.request.urlopen", return_value=_csv_response()):
            with patch.object(
                BlackRockPosition, "_fast_info_price", return_value=12.0
            ):
                pos = BlackRockPosition(
                    _ISIN, name="iShares Core MSCI EM IMI", shares=1, ctx=self.ctx
                )
        self.assertEqual(
            pos.countries(),
            [
                {"name": "Taiwan", "weight_pct": 28.04},
                {"name": "South Korea", "weight_pct": 21.77},
                {"name": "United States", "weight_pct": 2.0},
            ],
        )


class TestBlackRockFactoryRouting(unittest.TestCase):
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
            "name": "iShares Core MSCI EM IMI UCITS ETF (Acc)",
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

    def test_allowlisted_isin_and_existing_url_use_blackrock(self) -> None:
        with patch("position.factory.ishares_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, BlackRockPosition)

    def test_allowlisted_isin_ignores_name(self) -> None:
        with patch("position.factory.ishares_product_url_exists", return_value=True):
            with self._no_country_scrape():
                pos = self._factory(name="Core MSCI EM IMI UCITS ETF")
        self.assertIsInstance(pos, BlackRockPosition)

    def test_ishares_in_name_alone_stays_justetf(self) -> None:
        with patch("position.factory.ishares_product_url_exists") as exists:
            with self._no_country_scrape():
                pos = self._factory(
                    isin="IE00BDFK1573",
                    name="iShares $ Treasury Bond 1-3yr UCITS ETF",
                )
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, BlackRockPosition)

    def test_404_falls_back_to_justetf(self) -> None:
        with patch("position.factory.ishares_product_url_exists", return_value=False):
            with self._no_country_scrape():
                pos = self._factory()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, BlackRockPosition)

    def test_amundi_does_not_use_blackrock(self) -> None:
        with patch("position.factory.ishares_product_url_exists") as exists:
            with patch("position.factory.amundi_product_url_exists", return_value=True):
                with self._no_country_scrape():
                    pos = self._factory(
                        isin="IE000BI8OT95",
                        name="Amundi Core MSCI World UCITS ETF (Acc)",
                    )
        exists.assert_not_called()
        self.assertIsInstance(pos, AmundiPosition)
        self.assertNotIsInstance(pos, BlackRockPosition)

    def test_without_fetch_geosplit_skips_ishares_probe(self) -> None:
        self.ctx.config.fetch_geosplit = False
        with patch("position.factory.ishares_product_url_exists") as exists:
            pos = self._factory()
        exists.assert_not_called()
        self.assertIsInstance(pos, JustETFPosition)
        self.assertNotIsInstance(pos, BlackRockPosition)


if __name__ == "__main__":
    unittest.main()
