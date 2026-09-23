# SPDX-License-Identifier: AGPL-3.0-or-later
"""RegionalPortfolio sector consolidation: value-weighted dict + __add__."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        _isin="XX000STUB00",
    )


def _ctx():
    from context import AppConfig, RuntimeContext

    return RuntimeContext(config=AppConfig(plotter="web"))


def _regional(name, stubs) -> RegionalPortfolio:
    with patch("portfolio.portfolio._factory", side_effect=list(stubs)):
        return RegionalPortfolio(name, [{} for _ in stubs], ctx=_ctx())


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

    def test_positions_without_sectors_become_other(self) -> None:
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
        self.assertEqual(port.sectors, {"Technology": 0.5, "Other": 0.5})

    def test_zero_total_value_returns_empty(self) -> None:
        port = _regional(
            "R",
            [_stub(value=0.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        self.assertEqual(port.sectors, {})

    def test_empty_rows_become_other(self) -> None:
        port = _regional("R", [_stub(value=100.0, sectors=[])])
        self.assertEqual(port.sectors, {"Other": 1.0})


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
            other = NonRegionalPortfolio("N", [{}], ctx=_ctx())
        merged = regional + other
        self.assertNotIsInstance(merged, RegionalPortfolio)
        self.assertIsInstance(merged, Portfolio)
        self.assertAlmostEqual(merged.value, 150.0)
        # parent-class consolidation still applies to the merged positions
        self.assertAlmostEqual(merged.sectors["Technology"], 100.0 / 150.0)
        self.assertAlmostEqual(merged.sectors["Other"], 50.0 / 150.0)


def _refresh_stub(*, dmem=1.0, usavn=0.5, heal=None):
    ns = SimpleNamespace(
        value=100.0,
        dmem=dmem,
        usavn=usavn,
        sectors=lambda: [{"name": "Technology", "weight_pct": 100.0}],
        _short_name=None,
        _name="stub",
        _isin="XX000STUB00",
    )
    if heal is None:
        ns.refresh_geo = Mock(return_value=False)
    else:
        ns.refresh_geo = Mock(side_effect=lambda: heal(ns))
    return ns


class TestRefreshCountries(unittest.TestCase):
    def test_noop_rebuilds_identical(self) -> None:
        ns = _refresh_stub()
        port = _regional("R", [ns])
        before = (dict(port._geosplit_data), list(port._dmem), list(port._usavn))
        viz_before = port._geosplit_visualizer
        port.refresh_countries()
        ns.refresh_geo.assert_called_once_with()
        self.assertEqual(port._dmem, before[1])
        self.assertEqual(port._usavn, before[2])
        self.assertEqual(port._geosplit_data, before[0])
        self.assertEqual(port._geosplit_visualizer._data, before[0])

    def test_heal_rebuilds_visualizers(self) -> None:
        def heal(ns):
            ns.dmem = 0.6
            ns.usavn = 0.2
            return True

        ns = _refresh_stub(heal=heal)
        port = _regional("R", [ns])
        # Construction reflects dmem=1.0/usavn=0.5.
        self.assertAlmostEqual(port._geosplit_data["Equity US"], 0.5)
        viz_before = port._geosplit_visualizer
        port.refresh_countries()
        # Healed: developed=0.6, us_within=(100*0.2)/(100*0.6)=1/3.
        self.assertEqual(port._dmem, [0.6])
        self.assertAlmostEqual(port._geosplit_data["Equity US"], (1 / 3) * 0.6)
        self.assertEqual(port._geosplit_visualizer._data, port._geosplit_data)
        self.assertIsNot(port._geosplit_visualizer, viz_before)


if __name__ == "__main__":
    unittest.main()
