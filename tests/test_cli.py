"""CLI plotter selection and server config."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import cmd_update, load_server_config
from context import AppConfig
from visual import PLOTTERS, plotter_class
from visual.pie_chart import PieChart
from visual.web_chart import WebChart


class TestCliPlotFlags(unittest.TestCase):
    def test_plotter_class_web_and_pie(self) -> None:
        self.assertIs(plotter_class("web"), WebChart)
        self.assertIs(plotter_class("pie-chart"), PieChart)
        self.assertIs(PLOTTERS["web"], WebChart)
        with self.assertRaises(ValueError):
            plotter_class("nope")

    def test_cmd_update_builds_config_with_new_flags(self) -> None:
        import cli

        seen: dict = {}
        orig = cli.RuntimeContext

        class FakeCtx:
            def __init__(self, config) -> None:
                seen["config"] = config

        cli.RuntimeContext = FakeCtx  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = argparse.Namespace(
                    fetch_prices=True,
                    fetch_geosplit=False,
                    fetch_sectorsplit=False,
                    fetch_oskar=False,
                    fetch_scalable=False,
                    fetch_tr=False,
                    assets_file=Path(tmp) / "assets.json",
                    cache_file=Path(tmp) / "cache.json",
                    position_source="yfinance",
                    incognito=False,
                    plot="pie-chart",
                    log_level="INFO",
                )
                with patch(
                    "cli.run_update", side_effect=lambda ctx: seen.setdefault("ran", True)
                ), patch.object(
                    AppConfig, "server_from_ini", return_value=AppConfig().server
                ):
                    cmd_update(args)
        finally:
            cli.RuntimeContext = orig
        self.assertTrue(seen.get("ran"))
        config = seen["config"]
        self.assertTrue(config.fetch_prices)
        self.assertEqual(config.position_source, "yfinance")
        self.assertEqual(config.plotter, "pie-chart")
        self.assertTrue(str(config.cache_file).endswith("cache.json"))


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
