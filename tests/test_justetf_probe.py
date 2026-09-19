"""JustETF product-existence probe (mocked HTTP)."""

from __future__ import annotations

import json
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

from position.justetf_position import _chart_request, just_etf_product_url_exists

_ISIN = "IE000BI8OT95"


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", {}, BytesIO(b""))


def _chart_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestChartRequestShared(unittest.TestCase):
    def test_builds_versioned_chart_url(self) -> None:
        req = _chart_request(_ISIN, "EUR")
        url = req.get_full_url()
        self.assertIn(f"/api/etfs/{_ISIN}/performance-chart", url)
        self.assertIn("currency=EUR", url)
        self.assertIn("Chrome", req.get_header("User-agent"))


class TestJustEtfProductUrlExists(unittest.TestCase):
    def test_hit_on_eur_skips_usd(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_chart_response({"latestQuote": {"raw": 1.0}}),
        ) as opener:
            self.assertTrue(just_etf_product_url_exists(_ISIN))
        opener.assert_called_once()

    def test_eur_404_falls_through_to_usd(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            opener.side_effect = [
                _http_error("https://x", 404),
                _chart_response({"latestQuote": {"raw": 1.0}}),
            ]
            self.assertTrue(just_etf_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 2)

    def test_both_404_is_missing(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://x", 404),
        ) as opener:
            self.assertFalse(just_etf_product_url_exists(_ISIN))
        self.assertEqual(opener.call_count, 2)

    def test_network_error_fails_closed_without_retry(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ) as opener:
            self.assertFalse(just_etf_product_url_exists(_ISIN))
        opener.assert_called_once()

    def test_non_dict_payload_is_missing(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=_chart_response([1, 2]),
        ):
            self.assertFalse(just_etf_product_url_exists(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            self.assertFalse(just_etf_product_url_exists("   "))
        opener.assert_not_called()

    def test_input_is_normalized(self) -> None:
        seen: list = []
        real_request = _chart_request

        def spy(isin: str, currency: str):
            seen.append((isin, currency))
            return real_request(isin, currency)

        with (
            patch(
                "position.justetf_position._chart_request", side_effect=spy
            ),
            patch(
                "urllib.request.urlopen",
                return_value=_chart_response({"latestQuote": {"raw": 1.0}}),
            ),
        ):
            self.assertTrue(just_etf_product_url_exists("  ie000bi8ot95 "))
        self.assertEqual(seen, [(_ISIN, "EUR")])


if __name__ == "__main__":
    unittest.main()
