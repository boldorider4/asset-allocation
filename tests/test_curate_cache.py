# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``asalloc curate`` registry refresh command."""

from __future__ import annotations

import argparse
import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from cli.cli import cmd_curate, main
from cli.curate import CurateError, run_curate
from tests.store_helpers import (
    db_path,
    read_cache,
    write_cache,
    write_isin,
    write_positions,
)


class _Position:
    def __init__(
        self,
        countries: list[dict[str, object]] | None = None,
        sectors: list[dict[str, object]] | None = None,
    ) -> None:
        self._countries = countries or []
        self._sectors = sectors or []

    def countries(self) -> list[dict[str, object]]:
        return self._countries

    def sectors(self) -> list[dict[str, object]]:
        return self._sectors


def _rows(name: str) -> list[dict[str, object]]:
    return [{"name": name, "weight_pct": 100.0}]


def _run_files(tmp: str | Path) -> list[Path]:
    return sorted(Path(tmp).glob("asalloc.db.curate.*.json"))


class TestCurateRun(unittest.TestCase):
    def _cache(self, *isins: str) -> dict[str, object]:
        return {
            isin: {
                "price": 1.0,
                "countries": {"Old Country": 1.0},
                "sectors": {"Old Sector": 1.0},
            }
            for isin in isins
        }

    def _seed(self, db: Path, *isins: str) -> None:
        write_cache(db, self._cache(*isins))
        write_isin(db, {isin: None for isin in isins})

    def test_curates_every_registry_isin_without_a_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A", "IE00B")
            calls: list[str] = []

            def fake_factory(isin: str, **_kwargs):
                calls.append(isin)
                return _Position(_rows("New Country"), _rows("New Sector"))

            with patch("cli.curate.factory", side_effect=fake_factory):
                self.assertEqual(run_curate(db), 0)

            self.assertEqual(calls, ["IE00A", "IE00B"])
            cache = read_cache(db)
            for isin in ("IE00A", "IE00B"):
                self.assertEqual(
                    cache[isin]["countries"], {"New Country": 1.0}
                )
                self.assertEqual(cache[isin]["sectors"], {"New Sector": 1.0})
            self.assertEqual(_run_files(tmp), [])

    def test_registry_is_the_source_of_truth_not_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_cache(db, self._cache("IE00CACHE"))
            write_isin(db, {"IE00REG": None})
            calls: list[str] = []

            def fake_factory(isin: str, **_kwargs):
                calls.append(isin)
                return _Position(_rows("New Country"), _rows("New Sector"))

            with patch("cli.curate.factory", side_effect=fake_factory):
                self.assertEqual(run_curate(db), 0)

            self.assertEqual(calls, ["IE00REG"])
            cache = read_cache(db)
            self.assertEqual(
                cache["IE00REG"]["countries"], {"New Country": 1.0}
            )
            self.assertEqual(
                cache["IE00CACHE"]["countries"], {"Old Country": 1.0}
            )

    def test_user_filter_reads_user_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A", "IE00B", "IE00C")
            write_positions(
                db,
                {
                    "equity_portfolio": [
                        {"name": "A", "isin": "IE00A", "shares": 1}
                    ]
                },
                user="alice",
            )
            write_positions(
                db,
                {
                    "equity_portfolio": [
                        {"name": "B", "ISIN": "IE00B", "shares": 1}
                    ]
                },
                user="bob",
            )
            calls: list[str] = []

            def fake_factory(isin: str, **_kwargs):
                calls.append(isin)
                return _Position(_rows("New Country"), _rows("New Sector"))

            with patch("cli.curate.factory", side_effect=fake_factory):
                self.assertEqual(run_curate(db, user="alice"), 0)

            self.assertEqual(calls, ["IE00A"])
            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"New Country": 1.0}
            )
            self.assertEqual(
                cache["IE00B"]["countries"], {"Old Country": 1.0}
            )
            self.assertEqual(
                cache["IE00C"]["countries"], {"Old Country": 1.0}
            )

    def test_unknown_user_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            with self.assertRaises(CurateError):
                run_curate(db, user="nobody")
            self.assertEqual(_run_files(tmp), [])

    def test_empty_portfolio_curates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            write_positions(db, {}, user="alice")
            with patch("cli.curate.factory") as factory:
                self.assertEqual(run_curate(db, user="alice"), 0)
            factory.assert_not_called()

    def test_empty_countries_preserve_the_old_countries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            with patch(
                "cli.curate.factory",
                return_value=_Position([], _rows("New Sector")),
            ):
                self.assertEqual(run_curate(db), 0)
            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"Old Country": 1.0}
            )
            self.assertEqual(cache["IE00A"]["sectors"], {"New Sector": 1.0})

    def test_empty_sectors_preserve_the_old_sectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            with patch(
                "cli.curate.factory",
                return_value=_Position(_rows("New Country"), []),
            ):
                self.assertEqual(run_curate(db), 0)
            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"New Country": 1.0}
            )
            self.assertEqual(cache["IE00A"]["sectors"], {"Old Sector": 1.0})

    def test_failure_preserves_old_splits_and_keeps_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            with patch(
                "cli.curate.factory",
                side_effect=RuntimeError("boom"),
            ):
                self.assertEqual(run_curate(db), 1)

            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"Old Country": 1.0}
            )
            self.assertEqual(cache["IE00A"]["sectors"], {"Old Sector": 1.0})
            files = _run_files(tmp)
            self.assertEqual(len(files), 1)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["failed"], ["IE00A"])
            self.assertEqual(payload["examined"], [])
            self.assertEqual(payload["last_isin"], "IE00A")

    def test_invalid_cache_split_does_not_stop_curation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A", "IE00B")

            def fake_factory(isin: str, **_kwargs):
                if isin == "IE00A":
                    return _Position(
                        [{"name": "Invalid", "weight_pct": 200.0}],
                        _rows("New Sector"),
                    )
                return _Position(_rows("New Country"), _rows("New Sector"))

            with patch("cli.curate.factory", side_effect=fake_factory):
                self.assertEqual(run_curate(db), 1)

            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"Old Country": 1.0}
            )
            self.assertEqual(cache["IE00A"]["sectors"], {"Old Sector": 1.0})
            self.assertEqual(
                cache["IE00B"]["countries"], {"New Country": 1.0}
            )
            self.assertEqual(cache["IE00B"]["sectors"], {"New Sector": 1.0})
            files = _run_files(tmp)
            self.assertEqual(len(files), 1)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["failed"], ["IE00A"])
            self.assertEqual(payload["examined"], ["IE00B"])

    def test_resume_skips_examined_and_retries_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A", "IE00B", "IE00C")
            run_file = Path(tmp) / "resume.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(db.resolve()),
                        "user": None,
                        "examined": ["IE00A"],
                        "failed": ["IE00B"],
                        "last_isin": "IE00B",
                    }
                ),
                encoding="utf-8",
            )
            calls: list[str] = []

            def fake_factory(isin: str, **_kwargs):
                calls.append(isin)
                return _Position(_rows("New Country"), _rows("New Sector"))

            with patch("cli.curate.factory", side_effect=fake_factory):
                self.assertEqual(run_curate(db, run_file=run_file), 0)

            self.assertEqual(calls, ["IE00B", "IE00C"])
            self.assertFalse(run_file.exists())
            cache = read_cache(db)
            self.assertEqual(
                cache["IE00A"]["countries"], {"Old Country": 1.0}
            )
            self.assertEqual(
                cache["IE00B"]["countries"], {"New Country": 1.0}
            )
            self.assertEqual(
                cache["IE00C"]["countries"], {"New Country": 1.0}
            )

    def test_resume_ignores_stale_examined_isins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            run_file = Path(tmp) / "resume.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(db.resolve()),
                        "user": None,
                        "examined": ["IE00GONE"],
                        "failed": [],
                        "last_isin": "IE00GONE",
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "cli.curate.factory",
                return_value=_Position(_rows("New Country"), _rows("New Sector")),
            ) as factory:
                self.assertEqual(run_curate(db, run_file=run_file), 0)
            factory.assert_called_once()
            self.assertFalse(run_file.exists())

    def test_resume_user_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            write_positions(db, {}, user="alice")
            run_file = Path(tmp) / "resume.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(db.resolve()),
                        "user": "bob",
                        "examined": [],
                        "failed": [],
                        "last_isin": None,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CurateError):
                run_curate(db, user="alice", run_file=run_file)
            self.assertTrue(run_file.exists())

    def test_resume_database_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            self._seed(db, "IE00A")
            run_file = Path(tmp) / "resume.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(Path(tmp) / "other.db"),
                        "user": None,
                        "examined": [],
                        "failed": [],
                        "last_isin": None,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CurateError):
                run_curate(db, run_file=run_file)

    def test_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CurateError):
                run_curate(db_path(tmp))


class TestCurateCommand(unittest.TestCase):
    def _ini(
        self,
        tmp: Path,
        *,
        user: str | None = "alice",
        log_level: str | None = None,
    ) -> Path:
        db = db_path(tmp)
        ini = tmp / "config.ini"
        update_lines: list[str] = []
        if user is not None:
            update_lines.append(f"user = {user}")
        if log_level is not None:
            update_lines.append(f"log_level = {log_level}")
        update = (
            "[update]\n" + "\n".join(update_lines) + "\n"
            if update_lines
            else ""
        )
        ini.write_text(
            "[server]\nport = 8765\ndirectory = vis\n"
            f"db_file = {db}\n{update}",
            encoding="utf-8",
        )
        return ini

    def _call(self, **overrides) -> tuple[object, object]:
        values = {
            "config": None,
            "db_file": None,
            "user": None,
            "run": None,
        }
        values.update(overrides)
        with patch("cli.curate.run_curate", return_value=0) as run:
            with patch("cli.cli.configure_cli_logging"):
                with self.assertRaises(SystemExit) as exit_info:
                    cmd_curate(argparse.Namespace(**values))
        self.assertEqual(exit_info.exception.code, 0)
        return run, run.call_args

    def test_config_supplies_database_without_selecting_a_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp))
            _, call = self._call(config=ini)
            self.assertIsNone(call.kwargs["user"])
            self.assertEqual(call.args[0], db_path(tmp))

    def test_explicit_user_selects_the_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp))
            _, call = self._call(config=ini, user="bob")
            self.assertEqual(call.kwargs["user"], "bob")

    def test_db_file_only_curates_every_isin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flag = db_path(tmp)
            _, call = self._call(db_file=flag)
            self.assertEqual(call.args[0], flag)
            self.assertIsNone(call.kwargs["user"])

    def test_db_file_wins_and_config_user_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp))
            flag = Path(tmp) / "flag.db"
            _, call = self._call(config=ini, db_file=flag)
            self.assertEqual(call.args[0], flag)
            self.assertIsNone(call.kwargs["user"])

    def test_config_log_level_configures_logging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp), log_level="WARNING")
            with patch("cli.curate.run_curate", return_value=0):
                with patch("cli.cli.configure_cli_logging") as configure:
                    with self.assertRaises(SystemExit) as exit_info:
                        cmd_curate(
                            argparse.Namespace(
                                config=ini,
                                db_file=None,
                                user=None,
                                run=None,
                            )
                        )
            self.assertEqual(exit_info.exception.code, 0)
            configure.assert_called_once_with(logging.WARNING)

    def test_db_file_only_logging_defaults_to_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.curate.run_curate", return_value=0):
                with patch("cli.cli.configure_cli_logging") as configure:
                    with self.assertRaises(SystemExit) as exit_info:
                        cmd_curate(
                            argparse.Namespace(
                                config=None,
                                db_file=db_path(tmp),
                                user=None,
                                run=None,
                            )
                        )
            self.assertEqual(exit_info.exception.code, 0)
            configure.assert_called_once_with(logging.INFO)

    def test_log_level_argument_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = self._ini(Path(tmp), log_level="WARNING")
            with patch("cli.curate.run_curate", return_value=0):
                with patch("cli.cli.configure_cli_logging") as configure:
                    with self.assertRaises(SystemExit) as exit_info:
                        cmd_curate(
                            argparse.Namespace(
                                config=ini,
                                db_file=None,
                                user=None,
                                run=None,
                                log_level="DEBUG",
                            )
                        )
            self.assertEqual(exit_info.exception.code, 0)
            configure.assert_called_once_with(logging.DEBUG)

    def test_a_location_is_required(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as exit_info:
                cmd_curate(
                    argparse.Namespace(
                        config=None,
                        db_file=None,
                        user=None,
                        run=None,
                    )
                )
        self.assertEqual(exit_info.exception.code, 1)
        self.assertIn("--db-file", err.getvalue())

    def test_missing_config_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err):
                with self.assertRaises(SystemExit) as exit_info:
                    cmd_curate(
                        argparse.Namespace(
                            config=Path(tmp) / "nope.ini",
                            db_file=None,
                            user=None,
                            run=None,
                        )
                    )
            self.assertEqual(exit_info.exception.code, 1)
            self.assertIn("config", err.getvalue())

    def test_empty_user_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_info:
                    cmd_curate(
                        argparse.Namespace(
                            config=None,
                            db_file=db_path(tmp),
                            user="   ",
                            run=None,
                        )
                    )
            self.assertEqual(exit_info.exception.code, 2)

    def test_parser_dispatches_the_curate_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.curate.run_curate", return_value=0) as run:
                with self.assertRaises(SystemExit) as exit_info:
                    main(["curate", "--db-file", str(db_path(tmp))])
            self.assertEqual(exit_info.exception.code, 0)
            run.assert_called_once()

    def test_parser_accepts_log_level_after_curate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("cli.curate.run_curate", return_value=0):
                with patch("cli.cli.configure_cli_logging") as configure:
                    with self.assertRaises(SystemExit) as exit_info:
                        main(
                            [
                                "curate",
                                "--db-file",
                                str(db_path(tmp)),
                                "--log-level",
                                "DEBUG",
                            ]
                        )
            self.assertEqual(exit_info.exception.code, 0)
            configure.assert_called_with(logging.DEBUG)


if __name__ == "__main__":
    unittest.main()
