"""AppConfig / RuntimeContext: unified config + isolated per-run state."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from context import AppConfig, RuntimeContext
from visual.plot.pie_chart import PieChart
from visual.plot.web_chart import WebChart


def _update_ns(**overrides) -> argparse.Namespace:
    base = {
        "fetch_prices": False,
        "fetch_geosplit": False,
        "fetch_sectorsplit": False,
        "fetch_oskar": False,
        "fetch_scalable": False,
        "fetch_tr": False,
        "assets_file": None,
        "cache_file": None,
        "position_source": "justetf",
        "plot_clear": False,
        "plot_incognito": False,
        "plot": "web",
        "log_level": "INFO",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestServerFromIni(unittest.TestCase):
    def test_reads_full_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[server]\naddress = 0.0.0.0\nport = 9000\ndirectory = vis\n",
                encoding="utf-8",
            )
            server = AppConfig.server_from_ini(path)
        self.assertEqual(server.address, "0.0.0.0")
        self.assertEqual(server.port, 9000)
        self.assertEqual(server.directory, (Path(tmp) / "vis").resolve())

    def test_address_defaults_to_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text("[server]\nport = 8765\n", encoding="utf-8")
            server = AppConfig.server_from_ini(path)
        self.assertEqual(server.address, "localhost")

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                AppConfig.server_from_ini(Path(tmp) / "nope.ini")


class TestFromCli(unittest.TestCase):
    def test_flags_map_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text("[server]\nport = 8765\n", encoding="utf-8")
            config = AppConfig.from_cli(
                _update_ns(
                    fetch_prices=True,
                    fetch_geosplit=True,
                    fetch_oskar=True,
                    position_source="yfinance",
                    plot="pie-chart",
                    assets_file=Path(tmp) / "a.json",
                    cache_file=Path(tmp) / "c.json",
                ),
                ini_path=ini,
            )
        self.assertTrue(config.fetch_prices)
        self.assertTrue(config.fetch_geosplit)
        self.assertTrue(config.fetch_oskar)
        self.assertFalse(config.fetch_scalable)
        self.assertTrue(config.ignore_cache)
        self.assertEqual(config.position_source, "yfinance")
        self.assertEqual(config.plotter, "pie-chart")
        self.assertEqual(config.assets_file, Path(tmp) / "a.json")
        self.assertEqual(config.cache_file, Path(tmp) / "c.json")
        self.assertEqual(config.server.port, 8765)

    def test_defaults_point_at_repo_files(self) -> None:
        from context import DEFAULT_ASSETS_PATH, DEFAULT_CACHE_PATH

        config = AppConfig.from_cli(_update_ns())
        self.assertEqual(config.assets_file, DEFAULT_ASSETS_PATH)
        self.assertEqual(config.cache_file, DEFAULT_CACHE_PATH)
        self.assertEqual(config.position_source, "justetf")

    def test_invalid_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig.from_cli(_update_ns(position_source="bloomberg"))

    def test_invalid_plotter_raises(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig.from_cli(_update_ns(plot="3d"))


class TestRuntimeContextCache(unittest.TestCase):
    def _ctx(self, tmp: Path) -> RuntimeContext:
        from context import AppConfig as Cfg

        return RuntimeContext(
            config=Cfg(
                cache_file=tmp / "cache.json",
                assets_file=tmp / "assets.json",
            )
        )

    def test_missing_cache_file_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            cache = ctx.ensure_cache_loaded()
        self.assertEqual(cache, {})
        self.assertTrue(ctx.cache_loaded)
        self.assertFalse(ctx.cache_dirty)

    def test_flush_round_trips_and_clears_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.ensure_cache_loaded()
            ctx.cache["IE00X"] = {"price": 12.5}
            ctx.mark_cache_dirty()
            ctx.flush_cache()
            self.assertFalse(ctx.cache_dirty)
            saved = json.loads((Path(tmp) / "cache.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["IE00X"]["price"], 12.5)

    def test_flush_is_noop_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.ensure_cache_loaded()
            ctx.flush_cache()
        self.assertFalse((Path(tmp) / "cache.json").exists())

    def test_portfolio_flush_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.portfolio["equity_portfolio"] = [{"ISIN": "X", "value": 1.0}]
            ctx.flush_portfolio()
            ctx.portfolio.clear()
            ctx.load_portfolio()
        self.assertEqual(ctx.portfolio["equity_portfolio"][0]["ISIN"], "X")


class TestPlotterSelection(unittest.TestCase):
    def test_plotter_class(self) -> None:
        self.assertIs(
            RuntimeContext(config=AppConfig(plotter="web")).plotter_class(), WebChart
        )
        self.assertIs(
            RuntimeContext(config=AppConfig(plotter="pie-chart")).plotter_class(),
            PieChart,
        )

    def test_contexts_are_isolated(self) -> None:
        a = RuntimeContext(config=AppConfig())
        b = RuntimeContext(config=AppConfig())
        a.portfolio["x"] = []
        a.pending_oskar_shares[("i", 1.0)] = 2.0
        a.config.fetch_prices = True
        self.assertEqual(b.portfolio, {})
        self.assertEqual(b.pending_oskar_shares, {})
        self.assertFalse(b.config.fetch_prices)


class TestIncognitoOutputDir(unittest.TestCase):
    def setUp(self) -> None:
        from visual.plot.web_chart import WebChart

        self._orig_dir = WebChart.data_dir
        self._orig_counts = dict(WebChart._slug_counts)
        self._orig_seq = WebChart._plot_seq
        self.addCleanup(self._restore_output_state)

    def _restore_output_state(self) -> None:
        from visual.plot.web_chart import WebChart

        WebChart.data_dir = self._orig_dir
        WebChart._slug_counts = self._orig_counts
        WebChart._plot_seq = self._orig_seq

    def test_plain_run_writes_to_data_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            ctx = RuntimeContext(config=AppConfig(server=_server(server)))
            self.assertEqual(
                ctx.output_data_dir(incognito=False), server / "data" / "clear"
            )
            ctx.configure_web_output(incognito=False)
            from visual.plot.web_chart import WebChart

            self.assertEqual(WebChart.data_dir, server / "data" / "clear")
            WebChart(data={"A": 1.0}, title="Clear check").plot()
            self.assertTrue((server / "data" / "clear" / "01-clear-check.raw").is_file())

    def test_incognito_run_writes_to_data_incognito(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            ctx = RuntimeContext(
                config=AppConfig(plot_incognito=True, server=_server(server))
            )
            self.assertEqual(
                ctx.output_data_dir(incognito=True), server / "data" / "incognito"
            )
            ctx.configure_web_output(incognito=True)
            from visual.plot.web_chart import WebChart

            self.assertEqual(WebChart.data_dir, server / "data" / "incognito")

    def test_incognito_plot_creates_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            ctx = RuntimeContext(
                config=AppConfig(plot_incognito=True, server=_server(server))
            )
            ctx.configure_web_output(incognito=True)
            from visual.plot.web_chart import WebChart

            WebChart(data={"A": 1.0}, title="Incognito check").plot()
            raw = ctx.output_data_dir(incognito=True) / "01-incognito-check.raw"
            self.assertTrue(raw.is_file())

    def test_value_factor_defaults_to_one(self) -> None:
        self.assertEqual(RuntimeContext().value_factor, 1.0)


def _server(directory: Path):
    from context import ServerConfig

    return ServerConfig(port=8765, address="localhost", directory=directory)


if __name__ == "__main__":
    unittest.main()
