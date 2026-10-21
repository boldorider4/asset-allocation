# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the stores live, and the rules for it.

The stores live either in one SQLite database (``--db-file``) or in
loose JSON files (``--assets-file`` and friends). The backend in
``config.ini`` decides which one is in play, and a store with no
location (neither a flag nor an ini key) is a configuration error.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.stores import (
    StoreLocationMissing,
    db_file,
    required_stores,
    resolve_store_locations,
)


def _ini(tmp: Path, body: str) -> Path:
    path = tmp / "config.ini"
    path.write_text(body, encoding="utf-8")
    return path


class TestRequiredStores(unittest.TestCase):
    def test_json_needs_the_per_store_files(self) -> None:
        self.assertEqual(
            required_stores("json", "serve"), ("assets", "cache", "users")
        )
        self.assertEqual(
            required_stores("json", "update"), ("assets", "cache", "isin")
        )

    def test_the_database_covers_every_store(self) -> None:
        self.assertEqual(required_stores("sqlite", "serve"), ())
        self.assertEqual(required_stores("sqlite", "update"), ())

    def test_an_unknown_backend_falls_back_to_json(self) -> None:
        self.assertEqual(required_stores("mongodb", "serve"), ("assets", "cache", "users"))


class TestResolveStoreLocations(unittest.TestCase):
    def test_the_flag_wins_over_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(
                Path(tmp),
                "[update]\nassets_file = from-ini.json\ncache_file = c.json\n"
                "users_file = u.json\n",
            )
            located = resolve_store_locations(
                "json",
                "serve",
                ini_path=ini,
                flags={"assets_file": "/tmp/flag.json"},
            )
            self.assertEqual(located["assets"], Path("/tmp/flag.json"))
            self.assertEqual(located["cache"], Path(tmp) / "c.json")

    def test_the_ini_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(
                Path(tmp),
                "[update]\nassets_file = a.json\ncache_file = c.json\nusers_file = u.json\n",
            )
            located = resolve_store_locations("json", "serve", ini_path=ini)
            self.assertEqual(located["assets"], Path(tmp) / "a.json")
            self.assertEqual(located["users"], Path(tmp) / "u.json")

    def test_a_relative_ini_path_resolves_against_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(
                Path(tmp),
                "[update]\nassets_file = sub/a.json\ncache_file = c.json\nusers_file = u.json\n",
            )
            located = resolve_store_locations("json", "serve", ini_path=ini)
            self.assertEqual(located["assets"], Path(tmp) / "sub" / "a.json")

    def test_a_missing_store_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[update]\nassets_file = a.json\n")
            with self.assertRaises(StoreLocationMissing) as ctx:
                resolve_store_locations("json", "serve", ini_path=ini)
            self.assertIn("--cache-file", str(ctx.exception))
            self.assertIn("[update] cache_file", str(ctx.exception))

    def test_a_default_does_not_count(self) -> None:
        # No ini key, no flag: the built-in default points into the
        # repository, not into the deployment, so it is not a location.
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\nport = 8765\n")
            with self.assertRaises(StoreLocationMissing):
                resolve_store_locations("json", "serve", ini_path=ini)

    def test_the_database_backend_needs_no_per_store_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\nport = 8765\n")
            self.assertEqual(
                resolve_store_locations("sqlite", "serve", ini_path=ini), {}
            )


class TestDbFile(unittest.TestCase):
    def test_the_flag_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\ndb_file = from-ini.db\n")
            self.assertEqual(
                db_file("sqlite", ini_path=ini, flag_value="/tmp/flag.db"),
                Path("/tmp/flag.db"),
            )

    def test_the_ini_is_the_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\ndb_file = asalloc.db\n")
            self.assertEqual(
                db_file("sqlite", ini_path=ini), Path(tmp) / "asalloc.db"
            )

    def test_a_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = _ini(Path(tmp), "[server]\nport = 8765\n")
            with self.assertRaises(StoreLocationMissing) as ctx:
                db_file("sqlite", ini_path=ini)
            self.assertIn("--db-file", str(ctx.exception))

    def test_the_json_backend_has_no_database(self) -> None:
        self.assertIsNone(db_file("json"))


if __name__ == "__main__":
    unittest.main()
