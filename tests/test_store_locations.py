# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the SQLite database lives, and the rules for it.

The store lives in one database: ``--db-file`` or the ``[server]
db_file`` ini key. A database with no location is a configuration
error; the built-in default does not count.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cli.stores import StoreLocationMissing, db_file


def _ini(tmp: Path, body: str) -> Path:
    path = tmp / "config.ini"
    path.write_text(body, encoding="utf-8")
    return path


class TestDbFile(unittest.TestCase):
    def test_the_flag_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\ndb_file = from-ini.db\n")
            self.assertEqual(
                db_file(ini_path=ini, flag_value="/tmp/flag.db"),
                Path("/tmp/flag.db"),
            )

    def test_the_ini_is_the_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\ndb_file = asalloc.db\n")
            self.assertEqual(
                db_file(ini_path=ini), Path(tmp) / "asalloc.db"
            )

    def test_a_relative_ini_path_resolves_against_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\ndb_file = sub/asalloc.db\n")
            self.assertEqual(
                db_file(ini_path=ini), Path(tmp) / "sub" / "asalloc.db"
            )

    def test_a_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\nport = 8765\n")
            with self.assertRaises(StoreLocationMissing) as ctx:
                db_file(ini_path=ini)
            self.assertIn("--db-file", str(ctx.exception))

    def test_optional_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\nport = 8765\n")
            self.assertIsNone(db_file(ini_path=ini, required=False))


if __name__ == "__main__":
    unittest.main()
