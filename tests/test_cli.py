# SPDX-License-Identifier: AGPL-3.0-or-later
"""CLI plotter selection and server config."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.cli import cmd_serve, cmd_stop, cmd_update, load_server_config, main
from cli.context import AppConfig, ServerConfig
from visual import PLOTTERS, plotter_class
from visual.plot.pie_chart import PieChart
from visual.plot.web_chart import WebChart


class TestCliPlotFlags(unittest.TestCase):
    def test_plotter_class_web_and_pie(self) -> None:
        self.assertIs(plotter_class("web"), WebChart)
        self.assertIs(plotter_class("pie-chart"), PieChart)
        self.assertIs(PLOTTERS["web"], WebChart)
        with self.assertRaises(ValueError):
            plotter_class("nope")

    def test_cmd_update_builds_config_with_new_flags(self) -> None:
        import cli.cli as cli

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
                    db_file=Path(tmp) / "asalloc.db",
                    position_source="yfinance",
                    plot="pie-chart",
                    log_level="INFO",
                )
                with patch(
                    "cli.cli.run_update", side_effect=lambda ctx: seen.setdefault("ran", True)
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
        self.assertEqual(config.server.db_file, Path(tmp) / "asalloc.db")


class TestConfigFlag(unittest.TestCase):
    def _ini(self, tmp: Path) -> Path:
        path = tmp / "custom.ini"
        path.write_text(
            "[server]\nport = 8765\ndirectory = vis\ndb_file = asalloc.db\n"
            "[update]\nfetch_geosplit = True\n",
            encoding="utf-8",
        )
        return path

    def _ini_with_legacy_stores(self, tmp: Path) -> Path:
        """An ini using the removed per-store JSON paths."""
        path = tmp / "stores.ini"
        path.write_text(
            "[server]\nport = 8765\ndirectory = vis\n"
            "[update]\nassets_file = a.json\ncache_file = c.json\n"
            "users_file = u.json\nisin_file = i.json\n",
            encoding="utf-8",
        )
        return path

    def _ini_with_database(self, tmp: Path) -> Path:
        """An ini that points at one SQLite database."""
        path = tmp / "db.ini"
        path.write_text(
            "[server]\nport = 8765\ndirectory = vis\ndb_file = asalloc.db\n",
            encoding="utf-8",
        )
        return path

    def _serve_cmd(self, ini: Path, **overrides):
        """Run cmd_serve against a fake Popen; returns the child's argv."""
        calls: dict = {}

        def fake_popen(cmd, **kwargs):
            calls["cmd"] = list(cmd)
            raise RuntimeError("stop here")

        values = {
            "db_file": None,
            "config": ini,
        }
        values.update(overrides)
        namespace = argparse.Namespace(**values)
        try:
            with patch("cli.cli._find_server_pids", return_value=[]), patch(
                "cli.cli.subprocess.Popen", side_effect=fake_popen
            ):
                cmd_serve(namespace)
        except RuntimeError as exc:
            # Only the fake Popen's sentinel is swallowed: a real
            # configuration error has to reach the caller.
            if "stop here" not in str(exc):
                raise
        return calls["cmd"]

    def _update_ns(self, tmp: Path, config) -> argparse.Namespace:
        return argparse.Namespace(
            fetch_prices=None,
            fetch_geosplit=None,
            fetch_sectorsplit=None,
            fetch_oskar=None,
            fetch_scalable=None,
            fetch_tr=None,
            db_file=None,
            position_source="justetf",
            plot=None,
            log_level=None,
            config=config,
        )

    def test_config_file_feeds_from_cli(self) -> None:
        import cli.cli as cli

        seen: dict = {}
        orig = cli.RuntimeContext

        class FakeCtx:
            def __init__(self, config) -> None:
                seen["config"] = config

        cli.RuntimeContext = FakeCtx  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                args = self._update_ns(Path(tmp), Path(tmp) / "custom.ini")
                self._ini(Path(tmp))
                with patch(
                    "cli.cli.run_update", side_effect=lambda ctx: seen.setdefault("ran", True)
                ):
                    cmd_update(args)
        finally:
            cli.RuntimeContext = orig
        self.assertTrue(seen.get("ran"))
        self.assertTrue(seen["config"].fetch_geosplit)
        self.assertFalse(seen["config"].fetch_prices)

    def test_explicit_missing_config_raises(self) -> None:
        import cli.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            args = self._update_ns(Path(tmp), Path(tmp) / "nope.ini")
            with patch("cli.cli.run_update"):
                with self.assertRaises(FileNotFoundError):
                    cmd_update(args)

    def test_serve_propagates_config_env_and_layout(self) -> None:
        import cli.cli as cli

        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_database(Path(tmp))
            calls: dict = {}
            prev = os.environ.get("ASALLOC_CONFIG")

            def fake_popen(cmd, **kwargs):
                calls["cmd"] = list(cmd)
                calls["env"] = os.environ.get("ASALLOC_CONFIG")
                raise RuntimeError("stop here")

            try:
                with patch("cli.cli._find_server_pids", return_value=[]), patch(
                    "cli.cli.subprocess.Popen", side_effect=fake_popen
                ):
                    with self.assertRaises(RuntimeError):
                        cmd_serve(
                            argparse.Namespace(db_file=None, config=ini)
                        )
            finally:
                if prev is None:
                    os.environ.pop("ASALLOC_CONFIG", None)
                else:
                    os.environ["ASALLOC_CONFIG"] = prev
            self.assertEqual(calls["env"], str(ini))
            self.assertEqual(calls["cmd"][calls["cmd"].index("--plotter-data-dir") + 1], "data")
            self.assertNotIn("--plotter-clear-dir", calls["cmd"])

    def test_serve_follows_the_ini_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_database(Path(tmp))
            cmd = self._serve_cmd(ini)
            self.assertEqual(
                cmd[cmd.index("--db-file") + 1], str(Path(tmp) / "asalloc.db")
            )

    def test_serve_flag_wins_over_the_ini_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_database(Path(tmp))
            flag = Path(tmp) / "flag.db"
            cmd = self._serve_cmd(ini, db_file=flag)
            self.assertEqual(cmd[cmd.index("--db-file") + 1], str(flag))

    def test_serve_passes_only_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_database(Path(tmp))
            cmd = self._serve_cmd(ini)
            self.assertEqual(
                cmd[cmd.index("--db-file") + 1], str(Path(tmp) / "asalloc.db")
            )
            # The removed JSON stores stay out of the process list.
            for flag in (
                "--assets-file",
                "--cache-file",
                "--users-file",
                "--isin-registry-file",
            ):
                self.assertNotIn(flag, cmd)

    def test_serve_rejects_the_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "json.ini"
            ini.write_text(
                "[server]\nport = 8765\ndirectory = vis\nbackend = json\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                self._serve_cmd(ini)
            self.assertIn("JSON backend has been removed", str(ctx.exception))

    def test_legacy_store_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_legacy_stores(Path(tmp))
            with self.assertRaises(ValueError) as ctx:
                AppConfig.from_ini(ini)
            message = str(ctx.exception)
            self.assertIn("assets_file", message)
            self.assertIn("db_file", message)

    def test_serve_rejects_legacy_store_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini_with_legacy_stores(Path(tmp))
            with self.assertRaises(ValueError) as ctx:
                self._serve_cmd(ini)
            self.assertIn("assets_file", str(ctx.exception))
            self.assertIn("db_file", str(ctx.exception))

    def test_serve_needs_a_database_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "partial.ini"
            ini.write_text(
                "[server]\nport = 8765\ndirectory = vis\n",
                encoding="utf-8",
            )
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):
                    self._serve_cmd(ini)
            self.assertIn("--db-file", err.getvalue())

    def test_config_flag_parses_before_and_after_subcommand(self) -> None:
        seen: list = []
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp))
            with patch(
                "cli.cli.load_server_config",
                side_effect=lambda path=None: seen.append(path) or
                ServerConfig(port=1, address="localhost", directory=Path(tmp)),
            ):
                main(["--config", str(ini), "stop-serve"])
                main(["stop-serve", "--config", str(ini)])
        self.assertEqual(seen, [Path(ini), Path(ini)])


class TestClearSessions(unittest.TestCase):
    """``asalloc clear-sessions``: everybody logs in again."""

    def _ini_with_database(self, tmp: Path) -> Path:
        ini = tmp / "config.ini"
        ini.write_text(
            "[server]\nport = 8765\ndirectory = vis\n"
            f"db_file = {tmp / 'asalloc.db'}\n",
            encoding="utf-8",
        )
        return ini

    def test_it_clears_the_sessions_of_the_database(self) -> None:
        import sqlite3

        from visual.web.backend.session import login, read_session

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ini = self._ini_with_database(root)
            db_file = root / "asalloc.db"
            login(db_file, "sid", "alice")
            conn = sqlite3.connect(db_file)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1
                )
            finally:
                conn.close()
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                main(["--config", str(ini), "clear-sessions"])
            self.assertIn("cleared 1 session", out.getvalue())
            conn = sqlite3.connect(db_file)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0
                )
            finally:
                conn.close()
            self.assertIsNone(read_session(db_file, "asalloc_sid=sid"))

    def test_it_rejects_the_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ini = root / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\ndirectory = vis\nbackend = json\n",
                encoding="utf-8",
            )
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):
                    main(["--config", str(ini), "clear-sessions"])
            self.assertIn("JSON backend has been removed", err.getvalue())

    def test_only_the_expired_ones_can_be_cleared(self) -> None:
        from visual.web.backend.session import login

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ini = self._ini_with_database(root)
            db_file = root / "asalloc.db"
            login(db_file, "sid", "alice")
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                main(["--config", str(ini), "clear-sessions", "--expired-only"])
            # The live session is not expired: it stays.
            self.assertIn("cleared 0 expired session", out.getvalue())

    def test_a_database_without_a_location_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ini = root / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\ndirectory = vis\n",
                encoding="utf-8",
            )
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):
                    main(["--config", str(ini), "clear-sessions"])
            self.assertIn("db_file", err.getvalue())


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


class TestStop(unittest.TestCase):
    def _cfg(self, tmp: str):
        directory = Path(tmp) / "visualizer"
        directory.mkdir(parents=True, exist_ok=True)
        return ServerConfig(port=8765, address="localhost", directory=directory)

    def test_missing_pid_file_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.cli.load_server_config", return_value=self._cfg(tmp)):
                cmd_stop(argparse.Namespace())  # must not raise

    def test_malformed_pid_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "visualizer" / ".serve.pid"
            self._cfg(tmp)  # ensures directory exists
            pid_file.write_text("not-a-pid", encoding="utf-8")
            with patch("cli.cli.load_server_config", return_value=self._cfg(tmp)):
                cmd_stop(argparse.Namespace())
            self.assertFalse(pid_file.exists())

    def test_dead_pid_is_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            pid_file = cfg.directory / ".serve.pid"
            pid_file.write_text("999999999", encoding="utf-8")
            with patch("cli.cli.load_server_config", return_value=cfg):
                cmd_stop(argparse.Namespace())
            self.assertFalse(pid_file.exists())

    def test_live_server_pid_gets_sigterm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            pid_file = cfg.directory / ".serve.pid"
            proc = subprocess.Popen(["sleep", "60"])
            self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
            pid_file.write_text(str(proc.pid), encoding="utf-8")
            with (
                patch("cli.cli.load_server_config", return_value=cfg),
                patch("cli.cli._pid_is_server", return_value=True),
            ):
                cmd_stop(argparse.Namespace())
            self.assertIsNotNone(proc.wait(timeout=10))
            self.assertFalse(pid_file.exists())

    def test_foreign_pid_is_not_killed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            pid_file = cfg.directory / ".serve.pid"
            proc = subprocess.Popen(["sleep", "60"])
            self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
            pid_file.write_text(str(proc.pid), encoding="utf-8")
            with patch("cli.cli.load_server_config", return_value=cfg):
                cmd_stop(argparse.Namespace())  # sleep has no serve marker
            self.assertIsNone(proc.poll())
            self.assertFalse(pid_file.exists())
