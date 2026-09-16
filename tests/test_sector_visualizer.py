"""Sector chart prep: wedge filtering, breakdown wedges, and plot_sectors wiring."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from portfolio.portfolio import Portfolio
from portfolio.regional_portfolio import RegionalPortfolio
from visual import SECTOR_PALETTE


def _stub(*, value, sectors, dmem=1.0, usavn=0.5, short_name=None):
    return SimpleNamespace(
        value=value,
        dmem=dmem,
        usavn=usavn,
        sectors=lambda: sectors,
        _short_name=short_name,
        _name="stub",
        _isin="XX000STUB00",
    )


def _portfolio(cls, name, stubs):
    from context import AppConfig, RuntimeContext

    ctx = RuntimeContext(config=AppConfig(plotter="web"))
    with patch("portfolio.portfolio._factory", side_effect=list(stubs)):
        return cls(name, [{} for _ in stubs], ctx=ctx)


class _RecordingPlotter:
    """Stand-in for WebChart/PieChart; records plot() calls without writing files."""

    def __init__(self, data, title=None, closing_title=None, factor=None):
        self._data = data
        self._title = title
        self.plots = 0
        self.last_kwargs: dict = {}

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value

    @property
    def closing_title(self):
        return None

    @closing_title.setter
    def closing_title(self, value):
        pass

    def plot(self, **kwargs):
        self.plots += 1
        self.last_kwargs = dict(kwargs)

    def __add__(self, other):
        if not isinstance(other, _RecordingPlotter):
            return NotImplemented
        merged = _RecordingPlotter(
            data=dict(self._data) | dict(other._data),
            title=self._title,
        )
        return merged


class TestFilterSectorWedges(unittest.TestCase):
    def test_cap_keeps_largest_25(self) -> None:
        sectors = {f"S{i:02d}": 1.0 / 26 for i in range(26)}
        filtered = Portfolio._filter_sector_wedges(sectors)
        self.assertEqual(len(filtered), 26 - 1 + 1)  # 25 kept + Other
        self.assertAlmostEqual(filtered["Other"], 1.0 / 26)

    def test_floor_folds_small_wedges_into_other(self) -> None:
        filtered = Portfolio._filter_sector_wedges({"Technology": 0.9, "Tiny": 0.019})
        self.assertEqual(filtered, {"Technology": 0.9, "Other": 0.019})

    def test_floor_boundary_is_kept(self) -> None:
        filtered = Portfolio._filter_sector_wedges({"Technology": 0.98, "Edge": 0.02})
        self.assertEqual(filtered, {"Technology": 0.98, "Edge": 0.02})

    def test_keeper_other_absorbs_dropped_mass(self) -> None:
        filtered = Portfolio._filter_sector_wedges(
            {"Technology": 0.9, "Other": 0.05, "Tiny": 0.01}
        )
        self.assertAlmostEqual(filtered["Other"], 0.06)
        self.assertAlmostEqual(filtered["Technology"], 0.9)

    def test_empty_in_empty_out(self) -> None:
        self.assertEqual(Portfolio._filter_sector_wedges({}), {})


class TestSectorVisualizer(unittest.TestCase):
    def test_init_builds_filtered_visualizer(self) -> None:
        port = _portfolio(
            RegionalPortfolio,
            "R",
            [
                _stub(
                    value=100.0,
                    sectors=[
                        {"name": "Technology", "weight_pct": 99.0},
                        {"name": "Tiny", "weight_pct": 1.0},
                    ],
                )
            ],
        )
        self.assertEqual(
            port._sector_chart_data(), {"Technology": 0.99, "Other": 0.01}
        )

    def test_rowless_gold_becomes_commodities(self) -> None:
        port = _portfolio(
            RegionalPortfolio,
            "Inflation Hedge",
            [
                _stub(value=70.0, sectors=None, short_name="Gold"),
                _stub(value=30.0, sectors=None),
            ],
        )
        self.assertEqual(
            port._sectors, {"Commodities": 0.7, "Other": 0.3}
        )

    def test_rowless_gold_case_insensitive(self) -> None:
        port = _portfolio(
            RegionalPortfolio,
            "Inflation Hedge",
            [
                _stub(value=60.0, sectors=None, short_name="gold"),
                _stub(value=40.0, sectors=None, short_name="Silver"),
            ],
        )
        self.assertEqual(
            port._sectors, {"Commodities": 0.6, "Silver": 0.4}
        )

    def test_rowless_nameless_becomes_other(self) -> None:
        port = _portfolio(
            RegionalPortfolio, "Cash", [_stub(value=100.0, sectors=None)]
        )
        self.assertEqual(port._sectors, {"Other": 1.0})

    def test_add_small_wedges_filter_uniformly(self) -> None:
        # 30 distinct labels so the cap binds; the ~1% Commodities wedge
        # from Gold filters like everything else — no exemptions.
        labels = [f"S{i:02d}" for i in range(30)]
        equity = _portfolio(
            RegionalPortfolio,
            "Equity",
            [
                _stub(
                    value=990.0,
                    sectors=[
                        {"name": name, "weight_pct": 100.0 / 30} for name in labels
                    ],
                )
            ],
        )
        commodity = _portfolio(
            RegionalPortfolio,
            "Inflation Hedge",
            [_stub(value=10.0, sectors=None, short_name="Gold")],
        )
        merged = equity + commodity
        chart = merged._sector_chart_data()
        # 25 kept sector wedges + Other holding the 5 dropped sectors AND Gold.
        self.assertEqual(len(chart), 26)
        side_dropped = sum(
            w for _, w in sorted(equity._sectors.items(), key=lambda kv: -kv[1])[25:]
        )
        self.assertAlmostEqual(
            chart["Other"], side_dropped * 990.0 / 1000.0 + 10.0 / 1000.0
        )
        self.assertNotIn("Commodities", chart)

    def test_add_both_empty_folds_into_other(self) -> None:
        # No sector rows on either side: everything lands in Other.
        # Must use the recording plotter: the real WebChart would write a
        # stray *.raw file into the user's visualizer data dir.
        with patch("context.RuntimeContext.plotter_class", return_value=_RecordingPlotter):
            left = _portfolio(RegionalPortfolio, "A", [_stub(value=100.0, sectors=None)])
            right = _portfolio(RegionalPortfolio, "B", [_stub(value=100.0, sectors=None)])
            merged = left + right
            self.assertEqual(merged._sector_chart_data(), {"Other": 1.0})
            merged.plot_sectors()  # must not raise
            self.assertEqual(merged._sector_visualizer.plots, 1)
            self.assertEqual(merged._sector_visualizer._data, {"Other": 1.0})

    def test_chained_add_filters_uniformly(self) -> None:
        equity = _portfolio(
            RegionalPortfolio,
            "Equity",
            [_stub(value=4500.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        bonds = _portfolio(
            Portfolio,
            "Bonds",
            [_stub(value=250.0, sectors=[{"name": "Other", "weight_pct": 100.0}])],
        )
        commodity = _portfolio(
            Portfolio,
            "Inflation Hedge",
            [
                _stub(value=40.0, sectors=None, short_name="Gold"),
                _stub(value=10.0, sectors=None, short_name="EUR Infl.-Linkd"),
            ],
        )
        merged = (equity + bonds) + commodity
        # Sub-2% Gold and EUR Infl.-Linkd fold like everything else.
        self.assertAlmostEqual(merged._sector_chart_data()["Technology"], 4500.0 / 4800.0)
        self.assertAlmostEqual(
            merged._sector_chart_data()["Other"],
            250.0 / 4800.0 + 40.0 / 4800.0 + 10.0 / 4800.0,
        )
        self.assertNotIn("Commodities", merged._sector_chart_data())
        self.assertNotIn("Bonds", merged._sector_chart_data())
        self.assertAlmostEqual(sum(merged._sector_chart_data().values()), 1.0)

    def test_other_rows_side_merges_as_other(self) -> None:
        equity = _portfolio(
            RegionalPortfolio,
            "Equity",
            [_stub(value=4750.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        bonds = _portfolio(
            Portfolio,
            "Bonds",
            [_stub(value=250.0, sectors=[{"name": "Other", "weight_pct": 100.0}])],
        )
        merged = equity + bonds
        self.assertAlmostEqual(merged._sector_chart_data()["Technology"], 0.95)
        self.assertAlmostEqual(merged._sector_chart_data()["Other"], 0.05)
        self.assertAlmostEqual(sum(merged._sector_chart_data().values()), 1.0)

    def test_add_other_only_side_merges_as_other(self) -> None:
        equity = _portfolio(
            RegionalPortfolio,
            "Equity",
            [_stub(value=900.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        side = _portfolio(
            Portfolio, "Side", [_stub(value=100.0, sectors=[{"name": "Other", "weight_pct": 100.0}])]
        )
        merged = equity + side
        self.assertEqual(
            merged._sector_chart_data(), {"Technology": 0.9, "Other": 0.1}
        )

    def test_add_mixed_side_stays_union(self) -> None:
        equity = _portfolio(
            RegionalPortfolio,
            "Equity",
            [_stub(value=900.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
        )
        mixed = _portfolio(
            Portfolio,
            "Mixed",
            [
                _stub(
                    value=100.0,
                    sectors=[
                        {"name": "Technology", "weight_pct": 50.0},
                        {"name": "Government", "weight_pct": 50.0},
                    ],
                )
            ],
        )
        self.assertTrue(mixed._sectors)
        merged = equity + mixed
        # Government passes through the union (≥2%) like any other wedge.
        self.assertAlmostEqual(merged._sector_chart_data()["Government"], 0.05)
        self.assertNotIn("Mixed", merged._sector_chart_data())

    def test_plot_sectors_skips_empty_data(self) -> None:
        port = _portfolio(Portfolio, "E", [_stub(value=0.0, sectors=None)])
        self.assertEqual(port._sector_chart_data(), {})
        self.assertIsNone(port._sector_visualizer)
        port.plot_sectors()  # must not raise

    def test_plot_sectors_reuses_persistent_visualizer(self) -> None:
        with patch("context.RuntimeContext.plotter_class", return_value=_RecordingPlotter):
            port = _portfolio(
                RegionalPortfolio,
                "R",
                [_stub(value=100.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
            )
            viz = port._sector_visualizer
            self.assertIsNotNone(viz)
            with patch.object(
                Portfolio, "_filter_sector_wedges", side_effect=Portfolio._filter_sector_wedges
            ) as filt:
                port.plot_sectors()
                port.plot_sectors(title="T")
        filt.assert_not_called()
        self.assertIs(port._sector_visualizer, viz)
        self.assertEqual(viz.plots, 2)
        self.assertEqual(viz._title, "T")
        self.assertEqual(viz._data, {"Technology": 1.0})

    def test_merged_portfolio_has_persistent_visualizer(self) -> None:
        with patch("context.RuntimeContext.plotter_class", return_value=_RecordingPlotter):
            left = _portfolio(
                RegionalPortfolio,
                "A",
                [_stub(value=100.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])],
            )
            right = _portfolio(
                Portfolio, "B", [_stub(value=100.0, sectors=None, short_name="Gold")]
            )
            merged = left + right
            self.assertIsNotNone(merged._sector_visualizer)
            merged.plot_sectors()  # must not raise (previous AttributeError)
            self.assertEqual(merged._sector_visualizer.plots, 1)
            self.assertEqual(
                merged._sector_visualizer.last_kwargs.get("colors"), SECTOR_PALETTE
            )


if __name__ == "__main__":
    unittest.main()
