# SPDX-License-Identifier: AGPL-3.0-or-later
"""The JSON -> normalized SQLite import (``scripts/migrate_normalize_db.py``)."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.migrate_normalize_db import migrate  # noqa: E402

_ASSETS = {
    "alice": {
        "portfolio_name": "Alice Fund",
        "positions": {
            "equity_portfolio": [
                {
                    "name": "World ETF",
                    "short_name": "World",
                    "ISIN": "IE00TEST0001",
                    "shares": 2,
                    "value": 100.0,
                },
                {
                    "name": "No ISIN",
                    "shares": 1,
                    "value": 10.0,
                },
            ],
            "bond_portfolio": [
                {
                    "name": "Bond ETF",
                    "ISIN": "LU0000000001",
                    "value": 50.0,
                }
            ],
        },
    },
    "bob": {
        "portfolio_name": "Bob Fund",
        "positions": {
            "cash_portfolio": [
                {"name": "Cash", "short_name": "Cash", "value": 25.0}
            ]
        },
    },
}


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _tables(db_file: Path) -> set[str]:
    with sqlite3.connect(db_file) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


class TestMigrateNormalizeDb(unittest.TestCase):
    def _seed(self, root: Path) -> None:
        _write(root / "assets.json", _ASSETS)
        _write(root / "cache.json", {"IE00TEST0001": {"price": 50.0}})
        _write(root / "isin.json", {"IE00TEST0001": {"bucket": "equity_portfolio"}})
        _write(root / "users.json", {"alice": {"password": "x"}})
        _write(root / ".sessions.json", {"sid": {"user": "alice"}})

    def test_it_creates_the_normalized_tables_without_user_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            db_file = root / "asalloc.db"
            migrate(db_file, root)

            tables = _tables(db_file)
            self.assertIn("user_portfolios", tables)
            self.assertIn("user_positions", tables)
            self.assertNotIn("user_assets", tables)
            self.assertNotIn("positions", tables)

            with sqlite3.connect(db_file) as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(user_positions)")
                }
                self.assertIn("isin", columns)
                self.assertIn("short_name", columns)
                self.assertNotIn("price", columns)
                self.assertNotIn("id", columns)

    def test_it_preserves_the_uppercase_json_isin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            db_file = root / "asalloc.db"
            migrate(db_file, root)

            with sqlite3.connect(db_file) as conn:
                rows = conn.execute(
                    "SELECT username, bucket, position_idx, isin, short_name "
                    "FROM user_positions ORDER BY username, bucket, position_idx"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("alice", "bond_portfolio", 0, "LU0000000001", None),
                    ("alice", "equity_portfolio", 0, "IE00TEST0001", "World"),
                    ("alice", "equity_portfolio", 1, None, None),
                    ("bob", "cash_portfolio", 0, None, "Cash"),
                ],
            )

    def test_portfolio_names_are_imported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            db_file = root / "asalloc.db"
            migrate(db_file, root)

            with sqlite3.connect(db_file) as conn:
                rows = conn.execute(
                    "SELECT key, payload FROM user_portfolios ORDER BY key"
                ).fetchall()
            self.assertEqual([row[0] for row in rows], ["alice", "bob"])
            self.assertEqual(
                json.loads(rows[0][1])["portfolio_name"], "Alice Fund"
            )
            self.assertEqual(
                json.loads(rows[1][1])["portfolio_name"], "Bob Fund"
            )

    def test_it_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            db_file = root / "asalloc.db"
            migrate(db_file, root)
            first = {
                table: conn_count(db_file, table)
                for table in (
                    "user_portfolios",
                    "user_positions",
                    "cache",
                    "isin_registry",
                    "users",
                    "sessions",
                )
            }
            migrate(db_file, root)
            second = {
                table: conn_count(db_file, table)
                for table in first
            }
            self.assertEqual(second, first)

    def test_a_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            db_file = root / "never-created.db"
            imported = migrate(db_file, root, dry_run=True)
            self.assertFalse(db_file.exists())
            self.assertIn(("user_positions", 4), imported)

    def test_legacy_positions_table_is_converted_and_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_file = root / "legacy.db"
            with sqlite3.connect(db_file) as conn:
                conn.execute(
                    "CREATE TABLE positions ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username TEXT NOT NULL, bucket TEXT NOT NULL, "
                    "position_index INTEGER NOT NULL, name TEXT NOT NULL, "
                    "short_name TEXT, isin TEXT, value REAL, shares REAL, "
                    "broker TEXT, dmem REAL, dmem_other REAL, usavn REAL)"
                )
                conn.execute(
                    "INSERT INTO positions "
                    "(username, bucket, position_index, name, short_name, isin, "
                    "value, shares) VALUES "
                    "('alice', 'equity_portfolio', 0, 'Old ETF', 'Old', "
                    "'IE00OLD00001', 12.5, 3)"
                )
                conn.execute(
                    "CREATE TABLE position_countries ("
                    "position_id INTEGER NOT NULL REFERENCES positions(id) "
                    "ON DELETE CASCADE, name TEXT NOT NULL, "
                    "weight_pct REAL NOT NULL, PRIMARY KEY (position_id, name))"
                )
                conn.execute(
                    "CREATE TABLE position_sectors ("
                    "position_id INTEGER NOT NULL REFERENCES positions(id) "
                    "ON DELETE CASCADE, name TEXT NOT NULL, "
                    "weight_pct REAL NOT NULL, PRIMARY KEY (position_id, name))"
                )
                conn.execute(
                    "INSERT INTO position_countries VALUES (1, 'Germany', 100)"
                )
                conn.execute(
                    "INSERT INTO position_sectors VALUES (1, 'Technology', 100)"
                )
                conn.execute(
                    "CREATE TABLE user_assets (key TEXT PRIMARY KEY, payload TEXT)"
                )
                conn.commit()

            self.assertIn("sqlite_sequence", _tables(db_file))
            migrate(db_file, root)

            tables = _tables(db_file)
            self.assertNotIn("positions", tables)
            self.assertNotIn("position_countries", tables)
            self.assertNotIn("position_sectors", tables)
            self.assertNotIn("sqlite_sequence", tables)
            self.assertNotIn("user_assets", tables)
            with sqlite3.connect(db_file) as conn:
                row = conn.execute(
                    "SELECT username, bucket, position_idx, name, short_name, "
                    "isin, value, shares FROM user_positions"
                ).fetchone()
            self.assertEqual(
                row,
                (
                    "alice",
                    "equity_portfolio",
                    0,
                    "Old ETF",
                    "Old",
                    "IE00OLD00001",
                    12.5,
                    3.0,
                ),
            )


    def test_legacy_positions_without_isin_or_short_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_file = root / "legacy-old.db"
            with sqlite3.connect(db_file) as conn:
                conn.execute(
                    "CREATE TABLE positions ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username TEXT NOT NULL, bucket TEXT NOT NULL, "
                    "position_index INTEGER NOT NULL, name TEXT NOT NULL, "
                    "value REAL, shares REAL)"
                )
                conn.execute(
                    "INSERT INTO positions "
                    "(username, bucket, position_index, name, value, shares) "
                    "VALUES ('alice', 'equity_portfolio', 0, 'Old ETF', 12.5, 3)"
                )
                conn.commit()

            migrate(db_file, root)

            with sqlite3.connect(db_file) as conn:
                row = conn.execute(
                    "SELECT username, bucket, position_idx, name, short_name, "
                    "isin, value, shares FROM user_positions"
                ).fetchone()
            self.assertEqual(
                row,
                ("alice", "equity_portfolio", 0, "Old ETF", None, None, 12.5, 3.0),
            )


def conn_count(db_file: Path, table: str) -> int:
    with sqlite3.connect(db_file) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
