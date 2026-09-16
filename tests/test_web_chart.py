"""WebChart raw payloads, merge parity with PieChart, and example files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from visual import PLOTTERS, SECTOR_PALETTE
from visual.pie_chart import PieChart
from visual.web_chart import WebChart, _TAB10


class TestWebChart(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name) / "data"
        self._orig_dir = WebChart.data_dir
        self._orig_counts = dict(WebChart._slug_counts)
        self._orig_seq = WebChart._plot_seq
        WebChart.data_dir = self.data_dir
        WebChart._slug_counts = {}
        WebChart._plot_seq = 0

    def tearDown(self) -> None:
        WebChart.data_dir = self._orig_dir
        WebChart._slug_counts = self._orig_counts
        WebChart._plot_seq = self._orig_seq
        self._tmpdir.cleanup()

    def test_default_visualizer_is_web_chart(self) -> None:
        from context import AppConfig, RuntimeContext

        self.assertIs(PLOTTERS["web"], WebChart)
        self.assertIs(RuntimeContext(config=AppConfig(plotter="web")).plotter_class(), WebChart)

    def test_plot_writes_raw_json_wedges(self) -> None:
        chart = WebChart(
            data={"US": 0.42, "Ex-US": 0.38, "Emerging Markets": 0.20},
            title="Equity Portfolio: Regional Split",
            closing_title="Value: 1000.00",
            factor={"value": 1000, "unit": "Euro"},
        )
        chart.plot()
        path = self.data_dir / "01-equity-portfolio-regional-split.raw"
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["name"], "Equity Portfolio: Regional Split")
        self.assertEqual(payload["closing_title"], "Value: 1000.00")
        self.assertEqual(payload["factor"], {"value": 1000, "unit": "Euro"})
        wedges = payload["wedges"]
        self.assertEqual([w["label"] for w in wedges], ["US", "Ex-US", "Emerging Markets"])
        self.assertEqual([w["weight"] for w in wedges], [0.42, 0.38, 0.20])
        self.assertEqual([w["value"] for w in wedges], [420.0, 380.0, 200.0])
        self.assertTrue(all(w["unit"] == "Euro" for w in wedges))
        self.assertTrue(all(w["color"].startswith("#") and len(w["color"]) == 7 for w in wedges))
        self.assertEqual(wedges[0]["color"], "#1f77b4")

    def test_rerun_overwrites_same_slug(self) -> None:
        WebChart(data={"A": 1.0}, title="Same Title").plot()
        WebChart._slug_counts = {}
        WebChart._plot_seq = 0
        WebChart(data={"B": 1.0}, title="Same Title").plot()
        files = list(self.data_dir.glob("*.raw"))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["wedges"][0]["label"], "B")

    def test_collision_suffix_in_one_run(self) -> None:
        WebChart(data={"A": 1.0}, title="Same Title").plot()
        WebChart(data={"B": 1.0}, title="Same Title").plot()
        names = sorted(p.name for p in self.data_dir.glob("*.raw"))
        self.assertEqual(names, ["01-same-title.raw", "02-same-title-2.raw"])

    def test_filenames_reflect_plot_call_order(self) -> None:
        WebChart(data={"B": 1.0}, title="Zulu").plot()
        WebChart(data={"A": 1.0}, title="Alpha").plot()
        names = sorted(p.name for p in self.data_dir.glob("*.raw"))
        self.assertEqual(names, ["01-zulu.raw", "02-alpha.raw"])

    def test_sector_palette_differs_from_geosplit(self) -> None:
        self.assertGreaterEqual(len(SECTOR_PALETTE), 12)
        self.assertTrue(all(c.startswith("#") and len(c) == 7 for c in SECTOR_PALETTE))
        self.assertTrue(set(SECTOR_PALETTE).isdisjoint(_TAB10))

    def test_plot_uses_alternate_palette_when_passed(self) -> None:
        WebChart(data={"A": 0.6, "B": 0.4}, title="Palette").plot(
            colors=["#111111", "#222222"]
        )
        path = self.data_dir / "01-palette.raw"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            [w["color"] for w in payload["wedges"]], ["#111111", "#222222"]
        )

    def test_plot_defaults_to_tab10_without_palette(self) -> None:
        WebChart(data={"A": 0.6, "B": 0.4}, title="Default").plot()
        path = self.data_dir / "01-default.raw"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            [w["color"] for w in payload["wedges"]], list(_TAB10[:2])
        )

    def test_pie_chart_forwards_colors_to_matplotlib(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        seen: dict = {}

        class FakeAx:
            def pie(self, sizes, **kwargs):
                seen.update(kwargs)
                return ([], [], [])

            def axis(self, *args, **kwargs):
                pass

        class FakeFig:
            canvas = SimpleNamespace(manager=None)

            def suptitle(self, *args, **kwargs):
                pass

            def text(self, *args, **kwargs):
                pass

            def subplots_adjust(self, *args, **kwargs):
                pass

        with (
            patch("matplotlib.pyplot.subplots", return_value=(FakeFig(), FakeAx())),
            patch("matplotlib.pyplot.show"),
            patch("matplotlib.pyplot.pause"),
        ):
            PieChart(data={"A": 0.6, "B": 0.4}).plot(colors=["#111111", "#222222"])
            seen.clear()
            PieChart(data={"A": 0.6, "B": 0.4}).plot()
        self.assertNotIn("colors", seen)

    def test_add_matches_pie_chart_merge(self) -> None:
        left = {"Europe": 45.0, "Developed Markets": 23.0, "Emerging Markets": 32.0}
        right = {"Europe": 45.0, "Developed Markets": 10.0, "Emerging Markets": 45.0}
        factor = {"value": 100000, "unit": "USD"}
        pie = PieChart(data=left, title="Split #1", factor=factor) + PieChart(
            data=right, title="Split #2", factor=factor
        )
        web = WebChart(data=left, title="Split #1", factor=factor) + WebChart(
            data=right, title="Split #2", factor=factor
        )
        self.assertEqual(web._data, pie._data)
        self.assertEqual(web._title, pie._title)
        self.assertEqual(web._closing_title, pie._closing_title)
        self.assertEqual(web._factor, pie._factor)
        self.assertIsInstance(web, WebChart)

    def test_write_example_payload_schema(self) -> None:
        WebChart.write_example()
        files = sorted(self.data_dir.glob("*.raw"))
        self.assertEqual(len(files), 2)
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("name", payload)
            self.assertTrue(payload["name"])
            self.assertIsInstance(payload["wedges"], list)
            self.assertGreater(len(payload["wedges"]), 0)
            for wedge in payload["wedges"]:
                self.assertIn("label", wedge)
                self.assertIn("weight", wedge)
                self.assertIn("color", wedge)
                self.assertIn("value", wedge)
                self.assertIsInstance(wedge["weight"], (int, float))
                self.assertGreaterEqual(wedge["weight"], 0)
                self.assertGreaterEqual(wedge["value"], 0)
            self.assertIn("closing_title", payload)
