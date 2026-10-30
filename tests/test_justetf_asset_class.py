# SPDX-License-Identifier: AGPL-3.0-or-later
"""JustETF Investment focus parsing and asset-class mapping."""

from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from cli.common import (
    BOND_PORTFOLIO,
    CASH_PORTFOLIO,
    COMMODITY_PORTFOLIO,
    EQUITY_PORTFOLIO,
    FIXED_MATURITY_BOND_PORTFOLIO,
)
from position.justetf_position import (
    JustETFPosition,
    _asset_class_from_focus,
    _parse_investment_focus,
    just_etf_asset_class,
)

_ISIN = "IE000J0JU6M8"
_HTML = (
    "<html><body>"
    '<div data-testid="etf-basics_row_investment-focus">'
    '<span data-testid="etf-basics_label_investment-focus">'
    "Investment focus</span>"
    '<div class="val" data-testid="tl_etf-basics_value_investment-focus">'
    "Bonds, USD, World, Corporate, 7-10, Maturity bonds"
    "</div></div></body></html>"
)


class TestParseInvestmentFocus(unittest.TestCase):
    def test_parses_focus_value(self) -> None:
        self.assertEqual(
            _parse_investment_focus(_HTML),
            "Bonds, USD, World, Corporate, 7-10, Maturity bonds",
        )

    def test_unescapes_entities_and_strips_nested_tags(self) -> None:
        html = (
            '<div data-testid="tl_etf-basics_value_investment-focus" '
            'class="val">  Equity &amp; <span>Growth</span>\n</div>'
        )
        self.assertEqual(_parse_investment_focus(html), "Equity & Growth")

    def test_missing_or_empty_value_returns_none(self) -> None:
        self.assertIsNone(_parse_investment_focus("<html></html>"))
        self.assertIsNone(
            _parse_investment_focus(
                '<div data-testid="tl_etf-basics_value_investment-focus">'
                "   </div>"
            )
        )


class TestAssetClassFromFocus(unittest.TestCase):
    def test_supported_focus_labels(self) -> None:
        cases = {
            "Equity, Emerging Markets, Social/Environmental": EQUITY_PORTFOLIO,
            "Equities, World": EQUITY_PORTFOLIO,
            "Bonds, EUR, World, Corporate": BOND_PORTFOLIO,
            "Bond, EUR, World": BOND_PORTFOLIO,
            "Precious Metals, World": COMMODITY_PORTFOLIO,
            "Commodities, World": COMMODITY_PORTFOLIO,
            "Commodity, World": COMMODITY_PORTFOLIO,
            "Money Market, EUR, Europe": CASH_PORTFOLIO,
            "Bonds, USD, World, Corporate, 7-10, Maturity bonds": (
                FIXED_MATURITY_BOND_PORTFOLIO
            ),
            "Bonds, EUR, World, Corporate, Maturity 1-3": (
                FIXED_MATURITY_BOND_PORTFOLIO
            ),
        }
        for focus, expected in cases.items():
            with self.subTest(focus=focus):
                self.assertEqual(_asset_class_from_focus(focus), expected)

    def test_maturity_only_changes_bond_category(self) -> None:
        self.assertEqual(
            _asset_class_from_focus("Equity, World, Maturity"),
            EQUITY_PORTFOLIO,
        )

    def test_unknown_or_empty_focus_returns_none(self) -> None:
        self.assertIsNone(_asset_class_from_focus(""))
        self.assertIsNone(_asset_class_from_focus("Real Estate, World"))
        self.assertIsNone(_asset_class_from_focus("Mixed, World"))


class TestJustEtfAssetClass(unittest.TestCase):
    def test_fetches_profile_and_returns_bucket(self) -> None:
        with patch(
            "position.justetf_position._fetch_profile_page",
            return_value=(_HTML, "base", object()),
        ) as fetch:
            self.assertEqual(
                just_etf_asset_class(f" {_ISIN.lower()} "),
                FIXED_MATURITY_BOND_PORTFOLIO,
            )
        fetch.assert_called_once_with(
            _ISIN, JustETFPosition._COUNTRY_PROFILE_FRAGMENT
        )

    def test_http_error_returns_none(self) -> None:
        err = urllib.error.HTTPError(
            "https://www.justetf.com", 403, "Forbidden", {}, None
        )
        with patch(
            "position.justetf_position._fetch_profile_page", side_effect=err
        ):
            self.assertIsNone(just_etf_asset_class(_ISIN))

    def test_network_error_returns_none(self) -> None:
        with patch(
            "position.justetf_position._fetch_profile_page",
            side_effect=urllib.error.URLError("timeout"),
        ):
            self.assertIsNone(just_etf_asset_class(_ISIN))

    def test_missing_focus_returns_none(self) -> None:
        with patch(
            "position.justetf_position._fetch_profile_page",
            return_value=("<html></html>", "base", object()),
        ):
            self.assertIsNone(just_etf_asset_class(_ISIN))

    def test_empty_isin_skips_network(self) -> None:
        with patch("position.justetf_position._fetch_profile_page") as fetch:
            self.assertIsNone(just_etf_asset_class("   "))
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
