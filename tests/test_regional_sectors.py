"""RegionalPortfolio sector consolidation: value-weighted dict + __add__."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from portfolio.non_regional_portfolio import NonRegionalPortfolio
from portfolio.portfolio import Portfolio
from portfolio.regional_portfolio import RegionalPortfolio


def _stub(*, value, sectors, dmem=1.0, usavn=0.5):
    return SimpleNamespace(
        value=value,
        dmem=dmem,
        usavn=usavn,
        sectors=lambda: sectors,
        _short_name=None,
        _name="stub",
    )


def _regional(name, stubs) -> RegionalPortfolio:
    with patch("portfolio.portfolio._factory", side_effect=list(stubs)):
        return RegionalPortfolio(name, [{} for _ in stubs])


class TestConsolidateSectors(unittest.TestCase):
    def test_value_weighted_fractions(self) -> None:
        port = _regional(
            "R",
            [
                _stub(
                    value=300.0,
                    sectors=[
                        {"name": "Technology", "weight_pct": 50.0},
                        {"name": "Finance", "weight_pct": 50.0},
                    ],
                ),
                _stub(
                    value=100.0,
                    sectors=[{"name": "Technology", "weight_pct": 100.0}],
                ),
            ],
        )
        self.assertAlmostEqual(port.sectors["Technology"], 0.75 * 0.5 + 0.25 * 1.0)
        self.assertAlmostEqual(port.sectors["Finance"], 0.75 * 0.5)
        self.assertAlmostEqual(sum(port.sectors.values()), 1.0)

    def test_positions_without_sectors_are_skipped(self) -> None:
        port = _regional(
            "R",
            [
                _stub(value=100.0, sectors=None),
                _stub(
                    value=100.0,
                    sectors=[{"name": "Technology", "weight_pct": 100.0}],
                ),
            ],
        )
        self.assertEqual(port.sectors, {"Technology": 0.5})

    def test_zero_total_value_returns_empty(self) -> None:
        port = _regional(
            "R",
            [_stub(value=0.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        self.assertEqual(port.sectors, {})

    def test_no_sector_data_returns_empty(self) -> None:
        port = _regional("R", [_stub(value=100.0, sectors=[])])
        self.assertEqual(port.sectors, {})


class TestRegionalAdd(unittest.TestCase):
    def test_add_merges_sectors_value_weighted(self) -> None:
        left = _regional(
            "L",
            [_stub(value=300.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        right = _regional(
            "R",
            [_stub(value=100.0, sectors=[{"name": "Finance", "weight_pct": 100.0}])],
        )
        merged = left + right
        self.assertIsInstance(merged, RegionalPortfolio)
        self.assertEqual(merged._name, "L + R")
        self.assertEqual(len(merged._positions), 2)
        self.assertAlmostEqual(merged.value, 400.0)
        self.assertAlmostEqual(merged.sectors["Technology"], 0.75)
        self.assertAlmostEqual(merged.sectors["Finance"], 0.25)

    def test_add_mixed_portfolio_falls_back_to_base(self) -> None:
        regional = _regional(
            "R",
            [_stub(value=100.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        with patch("portfolio.portfolio._factory", side_effect=[_stub(value=50.0, sectors=None, dmem=None, usavn=None)]):
            other = NonRegionalPortfolio("N", [{}])
        merged = regional + other
        self.assertNotIsInstance(merged, RegionalPortfolio)
        self.assertIsInstance(merged, Portfolio)
        self.assertAlmostEqual(merged.value, 150.0)
        # parent-class consolidation still applies to the merged positions
        self.assertAlmostEqual(merged.sectors["Technology"], 100.0 / 150.0)


if __name__ == "__main__":
    unittest.main()
