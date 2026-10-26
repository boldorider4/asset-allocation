# SPDX-License-Identifier: AGPL-3.0-or-later
"""AppConfig / RuntimeContext: unified config + isolated per-run state."""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from cli.context import AppConfig, RuntimeContext
from tests.store_helpers import (
    make_context,
    read_cache,
    read_positions,
)
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
        "db_file": None,
        "position_source": "justetf",
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
            ini.write_text(
                "[server]\nport = 8765\ndb_file = asalloc.db\n",
                encoding="utf-8",
            )
            config = AppConfig.from_cli(
                _update_ns(
                    fetch_prices=True,
                    fetch_geosplit=True,
                    fetch_oskar=True,
                    position_source="yfinance",
                    plot="pie-chart",
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
        self.assertEqual(config.server.port, 8765)
        self.assertEqual(config.server.db_file, Path(tmp) / "asalloc.db")

    def test_db_file_flag_wins_over_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\ndb_file = from-ini.db\n",
                encoding="utf-8",
            )
            flag = Path(tmp) / "from-flag.db"
            config = AppConfig.from_cli(
                _update_ns(db_file=flag), ini_path=ini
            )
        self.assertEqual(config.server.db_file, flag)

    def test_defaults_point_at_repo_database(self) -> None:
        from cli.context import DEFAULT_DB_PATH

        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text("[server]\nport = 8765\n", encoding="utf-8")
            config = AppConfig.from_cli(_update_ns(), ini_path=ini)
        self.assertEqual(config.server.db_file, DEFAULT_DB_PATH)
        self.assertEqual(config.position_source, "justetf")

    def test_invalid_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig.from_cli(_update_ns(position_source="bloomberg"))

    def test_invalid_plotter_raises(self) -> None:
        with self.assertRaises(ValueError):
            AppConfig.from_cli(_update_ns(plot="3d"))


class TestUpdateFromIni(unittest.TestCase):
    def _ini(self, tmp: Path, body: str) -> Path:
        path = tmp / "config.ini"
        path.write_text(body, encoding="utf-8")
        return path

    def test_full_section_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ini(
                Path(tmp),
                "[server]\nport = 8765\n"
                "[update]\nfetch_prices = True\nfetch_geosplit = yes\n"
                "fetch_sectorsplit = on\n"
                "log_level = DEBUG\n",
            )
            values = AppConfig._update_from_ini(path)
        self.assertTrue(values["fetch_prices"])
        self.assertTrue(values["fetch_geosplit"])
        self.assertTrue(values["fetch_sectorsplit"])
        self.assertEqual(values["log_level"], "DEBUG")

    def test_legacy_store_keys_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ini(
                Path(tmp),
                "[server]\nport = 8765\n"
                "[update]\nassets_file = a.json\ncache_file = c.json\n"
                "users_file = u.json\nisin_file = i.json\n",
            )
            with self.assertRaises(ValueError) as ctx:
                AppConfig._update_from_ini(path)
        message = str(ctx.exception)
        self.assertIn("assets_file", message)
        self.assertIn("db_file", message)

    def test_missing_section_gives_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            values = AppConfig._update_from_ini(
                self._ini(Path(tmp), "[server]\nport = 8765\n")
            )
        self.assertFalse(any(values[k] for k in ("fetch_prices", "fetch_geosplit", "fetch_sectorsplit")))
        self.assertEqual(values["log_level"], "INFO")
        for legacy in ("assets_file", "cache_file", "isin_file", "users_file"):
            self.assertNotIn(legacy, values)

    def test_missing_file_gives_defaults(self) -> None:
        values = AppConfig._update_from_ini(Path("/nonexistent-dir-xyz/config.ini"))
        self.assertFalse(values["fetch_prices"])
        self.assertEqual(values["log_level"], "INFO")

    def test_invalid_boolean_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                AppConfig._update_from_ini(
                    self._ini(Path(tmp), "[update]\nfetch_prices = maybe\n")
                )

    def test_invalid_log_level_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                AppConfig._update_from_ini(
                    self._ini(Path(tmp), "[update]\nlog_level = VERBOSE\n")
                )


class TestPlotterFromIni(unittest.TestCase):
    def test_full_section_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[server]\nport = 8765\n"
                "[plotter]\ntype = pie-chart\noutput_dir = plots\n",
                encoding="utf-8",
            )
            kind, plotter = AppConfig._plotter_from_ini(path)
        self.assertEqual(kind, "pie-chart")
        self.assertEqual(plotter.output_dir, Path(tmp) / "plots")

    def test_missing_file_gives_defaults(self) -> None:
        kind, plotter = AppConfig._plotter_from_ini(Path("/nonexistent-dir-xyz/config.ini"))
        self.assertEqual(kind, "web")
        self.assertIsNone(plotter.output_dir)

    def test_invalid_type_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text("[plotter]\ntype = 3d\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                AppConfig._plotter_from_ini(path)


class TestFromIni(unittest.TestCase):
    def test_combined_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[server]\nport = 9000\ndirectory = vis\nplotter_data_dir = d\n"
                "[update]\nfetch_geosplit = True\nlog_level = WARNING\n"
                "[plotter]\ntype = web\noutput_dir = out\n",
                encoding="utf-8",
            )
            config = AppConfig.from_ini(path)
        self.assertEqual(config.server.port, 9000)
        self.assertEqual(config.server.data_dir, "d")
        self.assertTrue(config.fetch_geosplit)
        self.assertFalse(config.fetch_prices)
        self.assertEqual(config.log_level, "WARNING")
        self.assertEqual(config.plotter, "web")
        self.assertEqual(config.plotter_config.output_dir, Path(tmp) / "out")


class TestFromCliIniMerge(unittest.TestCase):
    def _ini(self, tmp: Path) -> Path:
        path = tmp / "config.ini"
        path.write_text(
            "[server]\nport = 8765\ndb_file = asalloc.db\n"
            "[update]\nfetch_geosplit = True\nfetch_prices = True\n"
            "log_level = DEBUG\n"
            "[plotter]\ntype = pie-chart\n",
            encoding="utf-8",
        )
        return path

    def test_unset_flags_fall_back_to_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ns = _update_ns(
                fetch_prices=None,
                fetch_geosplit=None,
                fetch_sectorsplit=None,
                fetch_oskar=None,
                fetch_scalable=None,
                fetch_tr=None,
                db_file=None,
                plot=None,
                log_level=None,
            )
            config = AppConfig.from_cli(ns, ini_path=self._ini(Path(tmp)))
        self.assertTrue(config.fetch_prices)
        self.assertTrue(config.fetch_geosplit)
        self.assertFalse(config.fetch_sectorsplit)
        self.assertEqual(config.plotter, "pie-chart")
        self.assertEqual(config.log_level, "DEBUG")
        self.assertEqual(config.server.db_file, Path(tmp) / "asalloc.db")

    def test_explicit_false_beats_ini_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ns = _update_ns(
                fetch_geosplit=False, fetch_prices=None
            )
            config = AppConfig.from_cli(ns, ini_path=self._ini(Path(tmp)))
        self.assertFalse(config.fetch_geosplit)
        # Unset flags still fall back.
        self.assertTrue(config.fetch_prices)

    def test_explicit_true_beats_ini_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ns = _update_ns(fetch_sectorsplit=True, plot="web")
            config = AppConfig.from_cli(ns, ini_path=self._ini(Path(tmp)))
        self.assertTrue(config.fetch_sectorsplit)
        self.assertEqual(config.plotter, "web")


class TestRuntimeContextCache(unittest.TestCase):
    def _ctx(self, tmp: Path) -> RuntimeContext:
        return make_context(tmp)

    def test_missing_cache_starts_empty(self) -> None:
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
            saved = read_cache(ctx.config.server.db_file)
        self.assertEqual(saved["IE00X"]["price"], 12.5)

    def test_flush_is_noop_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.ensure_cache_loaded()
            ctx.flush_cache()
            self.assertEqual(read_cache(ctx.config.server.db_file), {})

    def test_portfolio_persist_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.portfolio["equity_portfolio"] = [{"ISIN": "X", "value": 1.0}]
            ctx.persist_portfolio()
            stored = read_positions(ctx.config.server.db_file)
            self.assertEqual(stored["equity_portfolio"][0]["ISIN"], "X")
            ctx.portfolio.clear()
            ctx.load_portfolio()
        self.assertEqual(ctx.portfolio["equity_portfolio"][0]["ISIN"], "X")

    def test_persist_portfolio_drops_removed_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.portfolio["a"] = [{"ISIN": "X"}]
            ctx.portfolio["b"] = [{"ISIN": "Y"}]
            ctx.persist_portfolio()
            del ctx.portfolio["b"]
            ctx.persist_portfolio()
            self.assertEqual(
                set(read_positions(ctx.config.server.db_file)), {"a"}
            )
            ctx.portfolio.clear()
            ctx.load_portfolio()
        self.assertEqual(set(ctx.portfolio), {"a"})

    def test_persist_portfolio_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(Path(tmp))
            ctx.portfolio["equity_portfolio"] = [{"ISIN": "X", "value": 1.0}]
            out = Path(tmp) / "other.db"
            ctx.persist_portfolio(out)
            self.assertEqual(
                read_positions(out)["equity_portfolio"][0]["ISIN"], "X"
            )
            ctx.portfolio.clear()
            ctx.load_portfolio(out)
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


class TestOutputDataDir(unittest.TestCase):
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

    def test_defaults_to_server_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            ctx = RuntimeContext(config=AppConfig(server=_server(server)))
            self.assertEqual(ctx.output_data_dir(), server / "data" / "default")

    def test_output_dir_overrides_serve_tree(self) -> None:
        from cli.context import PlotterConfig, ServerConfig

        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            out = Path(tmp) / "plots"
            config = AppConfig(
                server=_server(server),
                plotter_config=PlotterConfig(output_dir=out),
            )
            ctx = RuntimeContext(config=config)
            self.assertEqual(ctx.output_data_dir(), out / "default")

    def test_server_data_dir_renames_legacy_tree(self) -> None:
        from cli.context import ServerConfig

        with tempfile.TemporaryDirectory() as tmp:
            server = ServerConfig(
                port=8765,
                address="localhost",
                directory=Path(tmp) / "visualizer",
                data_dir="d",
            )
            ctx = RuntimeContext(config=AppConfig(server=server))
            self.assertEqual(
                ctx.output_data_dir(),
                Path(tmp) / "visualizer" / "d" / "default",
            )

    def test_webchart_writes_to_server_data(self) -> None:
        from visual.plot.web_chart import WebChart

        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp) / "visualizer"
            ctx = RuntimeContext(config=AppConfig(server=_server(server)))
            WebChart.data_dir = ctx.output_data_dir()
            WebChart._slug_counts = {}
            WebChart._plot_seq = 0
            self.assertEqual(WebChart.data_dir, server / "data" / "default")
            WebChart(data={"A": 1.0}, title="Clear check").plot()
            self.assertTrue(
                (server / "data" / "default" / "01-clear-check.raw").is_file()
            )


def _server(directory: Path):
    from cli.context import ServerConfig

    return ServerConfig(port=8765, address="localhost", directory=directory)


if __name__ == "__main__":
    unittest.main()
