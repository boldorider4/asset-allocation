# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clear/incognito plot passes: template titles, per-pass factors and dirs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli.context import AppConfig, RuntimeContext, ServerConfig
from portfolio.portfolio import Portfolio
from portfolio.regional_portfolio import RegionalPortfolio


def _stub(*, value, sectors=None, dmem=1.0, usavn=0.5, short_name=None):
    return SimpleNamespace(
        value=value,
        dmem=dmem,
        usavn=usavn,
        sectors=lambda: sectors,
        _short_name=short_name,
        _name="stub",
        _isin="XX000STUB00",
    )


class _RecordingPlotter:
    """Stand-in chart class with factor support; records plot() calls."""

    def __init__(self, data, title=None, closing_title=None, factor=None):
        self._data = data
        self._title = title
        self._closing_title = closing_title
        self._factor = factor
        self.plots = 0

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value

    @property
    def closing_title(self):
        return self._closing_title

    @closing_title.setter
    def closing_title(self, value):
        self._closing_title = value

    @property
    def factor(self):
        return self._factor

    @factor.setter
    def factor(self, value):
        self._factor = value

    def plot(self, **kwargs):
        self.plots += 1


def _rec_portfolio(ctx, stubs, cls=Portfolio, name="P"):
    """Build a portfolio whose visualizers are recording doubles (no files)."""
    with (
        patch.object(ctx, "plotter_class", return_value=_RecordingPlotter),
        patch("portfolio.portfolio._factory", side_effect=list(stubs)),
    ):
        return cls(name, [{} for _ in stubs], ctx=ctx)


class TestPlotIncognitoKwarg(unittest.TestCase):
    def test_closing_template_uses_clear_then_scaled_totals(self) -> None:
        ctx = RuntimeContext(config=AppConfig())
        ctx.value_factor = 2.5
        port = _rec_portfolio(
            ctx, [_stub(value=100.0, sectors=None)], cls=RegionalPortfolio
        )
        port.plot_geosplit(closing_title="Total Value: {tot_value}", incognito=False)
        self.assertEqual(port._geosplit_visualizer.closing_title, "Total Value: 100.00")
        self.assertEqual(
            port._geosplit_visualizer.factor, {"value": 100.0, "unit": "Euro"}
        )
        port.plot_geosplit(closing_title="Total Value: {tot_value}", incognito=True)
        self.assertEqual(port._geosplit_visualizer.closing_title, "Total Value: 250.00")
        self.assertEqual(
            port._geosplit_visualizer.factor, {"value": 250.0, "unit": "Euro"}
        )

    def test_plain_titles_pass_through_unchanged(self) -> None:
        ctx = RuntimeContext(config=AppConfig())
        ctx.value_factor = 2.5
        port = _rec_portfolio(ctx, [_stub(value=100.0, sectors=None)])
        port.plot_sectors(closing_title="Net Worth: 123", incognito=True)
        self.assertEqual(port._sector_visualizer.closing_title, "Net Worth: 123")
        port.plot_sectors(closing_title="Net Worth: 123", incognito=False)
        self.assertEqual(port._sector_visualizer.closing_title, "Net Worth: 123")

    def test_factor_sync_is_idempotent_across_passes(self) -> None:
        ctx = RuntimeContext(config=AppConfig())
        ctx.value_factor = 3.0
        port = _rec_portfolio(
            ctx, [_stub(value=40.0, sectors=None)], cls=RegionalPortfolio
        )
        port.plot_geosplit(incognito=True)
        self.assertEqual(port._geosplit_visualizer.factor["value"], 120.0)
        port.plot_geosplit(incognito=False)
        self.assertEqual(port._geosplit_visualizer.factor["value"], 40.0)

    def test_stored_values_stay_clear(self) -> None:
        ctx = RuntimeContext(config=AppConfig())
        ctx.value_factor = 2.5
        port = _rec_portfolio(ctx, [_stub(value=100.0, sectors=None)])
        port.plot_geosplit(closing_title="Total Value: {tot_value}", incognito=True)
        self.assertEqual(port.value, 100.0)
        self.assertEqual(port.total_value, 100.0)


class TestBothPassesWriteBothDirs(unittest.TestCase):
    def test_clear_and_incognito_raw_payloads(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = Path(holder.name)
        server = tmp / "visualizer"
        ctx = RuntimeContext(
            config=AppConfig(
                plot_clear=True,
                plot_incognito=True,
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
                server=ServerConfig(port=8765, address="localhost", directory=server),
            )
        )
        ctx.value_factor = 2.0
        from visual.plot.web_chart import WebChart

        orig = (WebChart.data_dir, dict(WebChart._slug_counts), WebChart._plot_seq)
        self.addCleanup(
            lambda: (
                setattr(WebChart, "data_dir", orig[0]),
                WebChart._slug_counts.update(orig[1]),
                setattr(WebChart, "_plot_seq", orig[2]),
            )
        )
        stubs = [_stub(value=100.0, sectors=[{"name": "Technology", "weight_pct": 100.0}])]
        with patch("portfolio.portfolio._factory", side_effect=list(stubs)):
            port = RegionalPortfolio("R", [{}], ctx=ctx)
        for incognito in (False, True):
            ctx.configure_web_output(incognito=incognito)
            port.plot_geosplit(
                title="Complete Portfolio",
                closing_title="Net Worth: {tot_value}",
                incognito=incognito,
            )
        clear_raw = server / "data" / "clear" / "01-complete-portfolio.raw"
        incognito_raw = server / "data" / "incognito" / "01-complete-portfolio.raw"
        self.assertTrue(clear_raw.is_file())
        self.assertTrue(incognito_raw.is_file())
        clear_payload = json.loads(clear_raw.read_text(encoding="utf-8"))
        incognito_payload = json.loads(incognito_raw.read_text(encoding="utf-8"))
        self.assertEqual(clear_payload["closing_title"], "Net Worth: 100.00")
        self.assertEqual(incognito_payload["closing_title"], "Net Worth: 200.00")
        self.assertEqual(clear_payload["factor"], {"value": 100.0, "unit": "Euro"})
        self.assertEqual(incognito_payload["factor"], {"value": 200.0, "unit": "Euro"})
        # Wedge fractions are scale-invariant.
        self.assertEqual(
            [w["weight"] for w in clear_payload["wedges"]],
            [w["weight"] for w in incognito_payload["wedges"]],
        )


if __name__ == "__main__":
    unittest.main()
