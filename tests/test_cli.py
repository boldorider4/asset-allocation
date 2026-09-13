"""CLI plotter selection and server config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cli import load_server_config
from visual import get_plotter, set_plotter
from visual.pie_chart import PieChart
from visual.web_chart import WebChart


class TestCliPlotFlags(unittest.TestCase):
    def tearDown(self) -> None:
        set_plotter("web")

    def test_set_plotter_web_and_pie(self) -> None:
        set_plotter("web")
        self.assertIs(get_plotter(), WebChart)
        set_plotter("pie-chart")
        self.assertIs(get_plotter(), PieChart)


class TestServerConfig(unittest.TestCase):
    def test_address_defaults_to_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text("[server]\nport = 8765\n", encoding="utf-8")
            cfg = load_server_config(path)
            self.assertEqual(cfg.address, "localhost")
            self.assertEqual(cfg.port, 8765)

    def test_reads_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[server]\naddress = 0.0.0.0\nport = 9000\ndirectory = vis\n",
                encoding="utf-8",
            )
            cfg = load_server_config(path)
            self.assertEqual(cfg.address, "0.0.0.0")
            self.assertEqual(cfg.port, 9000)
            self.assertEqual(cfg.directory, (Path(tmp) / "vis").resolve())
