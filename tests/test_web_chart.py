"""WebChart raw payloads, merge parity with PieChart, and example files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from visual import DEFAULT_VISUALIZER
from visual.pie_chart import PieChart
from visual.web_chart import WebChart


class TestWebChart(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmpdir.name) / "data"
        self._orig_dir = WebChart.data_dir
        self._orig_counts = dict(WebChart._slug_counts)
        WebChart.data_dir = self.data_dir
        WebChart._slug_counts = {}

    def tearDown(self) -> None:
        WebChart.data_dir = self._orig_dir
        WebChart._slug_counts = self._orig_counts
        self._tmpdir.cleanup()

    def test_default_visualizer_is_web_chart(self) -> None:
        self.assertIs(DEFAULT_VISUALIZER, WebChart)

    def test_plot_writes_raw_json_wedges(self) -> None:
        chart = WebChart(
            data={"US": 0.42, "Ex-US": 0.38, "Emerging Markets": 0.20},
            title="Equity Portfolio: Regional Split",
            factor={"value": 1000, "unit": "Euro"},
        )
        chart.plot()
        path = self.data_dir / "equity-portfolio-regional-split.raw"
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["name"], "Equity Portfolio: Regional Split")
        self.assertEqual(payload["factor"], {"value": 1000, "unit": "Euro"})
        wedges = payload["wedges"]
        self.assertEqual([w["label"] for w in wedges], ["US", "Ex-US", "Emerging Markets"])
        self.assertEqual([w["weight"] for w in wedges], [0.42, 0.38, 0.20])
        self.assertTrue(all(w["color"].startswith("#") and len(w["color"]) == 7 for w in wedges))
        self.assertEqual(wedges[0]["color"], "#1f77b4")

    def test_rerun_overwrites_same_slug(self) -> None:
        WebChart(data={"A": 1.0}, title="Same Title").plot()
        WebChart._slug_counts = {}
        WebChart(data={"B": 1.0}, title="Same Title").plot()
        files = list(self.data_dir.glob("*.raw"))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["wedges"][0]["label"], "B")

    def test_collision_suffix_in_one_run(self) -> None:
        WebChart(data={"A": 1.0}, title="Same Title").plot()
        WebChart(data={"B": 1.0}, title="Same Title").plot()
        names = sorted(p.name for p in self.data_dir.glob("*.raw"))
        self.assertEqual(names, ["same-title-2.raw", "same-title.raw"])

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
                self.assertIsInstance(wedge["weight"], (int, float))
                self.assertGreaterEqual(wedge["weight"], 0)
