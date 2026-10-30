# SPDX-License-Identifier: AGPL-3.0-or-later
"""JustETF product-name parsing and profile-page reuse."""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from cli.context import AppConfig, RuntimeContext, ServerConfig
from position.justetf_position import (
    JustETFPosition,
    _parse_etf_name,
    just_etf_name,
)

_ISIN = "IE00BHZPJ239"
_HTML = (
    "<html><head><title>"
    "iShares MSCI EM CTB Enhanced ESG UCITS ETF USD (Acc) | EDM2 | IE00BHZPJ239"
    "</title></head></html>"
)


class TestParseEtfName(unittest.TestCase):
    def test_parses_name_before_first_pipe(self) -> None:
        self.assertEqual(
            _parse_etf_name(_HTML, _ISIN),
            "iShares MSCI EM CTB Enhanced ESG UCITS ETF USD (Acc)",
        )

    def test_parses_isin_only_title(self) -> None:
        html = f"<title>Some Fund | {_ISIN}</title>"
        self.assertEqual(_parse_etf_name(html, _ISIN), "Some Fund")

    def test_unescapes_html_entities_and_normalizes_whitespace(self) -> None:
        html = f"<title>  Foo &amp;   Bar  | {_ISIN} </title>"
        self.assertEqual(_parse_etf_name(html, _ISIN), "Foo & Bar")

    def test_rejects_missing_or_generic_title(self) -> None:
        self.assertIsNone(_parse_etf_name("<html></html>", _ISIN))
        self.assertIsNone(_parse_etf_name("<title>justETF</title>", _ISIN))
        self.assertIsNone(
            _parse_etf_name("<title>Some Fund | WKN</title>", _ISIN)
        )

    def test_rejects_title_without_the_isin(self) -> None:
        self.assertIsNone(
            _parse_etf_name("<title>Some Fund | WKN | DE0001234567</title>", _ISIN)
        )

    def test_empty_isin_returns_none(self) -> None:
        self.assertIsNone(_parse_etf_name(_HTML, "   "))


class TestJustEtfName(unittest.TestCase):
    def test_fetches_profile_and_parses_name(self) -> None:
        with patch(
            "position.justetf_position._fetch_profile_page",
            return_value=(_HTML, "base", object()),
        ) as fetch:
            self.assertEqual(
                just_etf_name(" ie00bhzpj239 "),
                "iShares MSCI EM CTB Enhanced ESG UCITS ETF USD (Acc)",
            )
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], _ISIN)

    def test_http_error_returns_none(self) -> None:
        err = urllib.error.HTTPError(
            "https://www.justetf.com", 403, "Forbidden", {}, None
        )
        with patch(
            "position.justetf_position._fetch_profile_page", side_effect=err
        ):
            self.assertIsNone(just_etf_name(_ISIN))

    def test_network_error_returns_none(self) -> None:
        with patch(
            "position.justetf_position._fetch_profile_page",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertIsNone(just_etf_name(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("position.justetf_position._fetch_profile_page") as fetch:
            self.assertIsNone(just_etf_name("   "))
        fetch.assert_not_called()


class TestProfilePageReuse(unittest.TestCase):
    def test_countries_and_sectors_share_one_profile_get(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = Path(holder.name)
        ctx = RuntimeContext(
            config=AppConfig(
                fetch_geosplit=False,
                fetch_sectorsplit=False,
                server=ServerConfig(
                    port=0,
                    address="localhost",
                    directory=tmp,
                    db_file=tmp / "asalloc.db",
                ),
            )
        )
        seed = (_HTML, "base", object())
        with patch(
            "position.justetf_position._fetch_profile_page",
            return_value=seed,
        ) as fetch:
            position = JustETFPosition(_ISIN, name="EM ETF", price=12.0, ctx=ctx)
            first = position._holdings_seed_page(
                profile_fragment=position._COUNTRY_PROFILE_FRAGMENT
            )
            second = position._holdings_seed_page(
                profile_fragment=position._SECTOR_PROFILE_FRAGMENT
            )
        self.assertIs(first, second)
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
