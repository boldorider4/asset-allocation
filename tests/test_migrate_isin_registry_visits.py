# SPDX-License-Identifier: AGPL-3.0-or-later
"""The isin_registry.visits migration script."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SCRIPT = REPO_ROOT / "scripts" / "v3.0.0_add_isin_registry_visits.py"
_SPEC = importlib.util.spec_from_file_location(
    "add_isin_registry_visits", _SCRIPT
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
add_visits_column = _MODULE.add_visits_column


def _old_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE isin_registry "
            "(isin TEXT PRIMARY KEY, issuer TEXT, bucket TEXT, product_ref TEXT)"
        )
        conn.execute(
            "INSERT INTO isin_registry VALUES (?, ?, ?, ?)",
            ("IE00BKM4GZ66", "ishares", "equity_portfolio", "264659"),
        )


def _columns(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(isin_registry)")
        }


class TestAddIsinRegistryVisits(unittest.TestCase):
    def test_adds_column_and_preserves_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            _old_db(db)
            self.assertTrue(add_visits_column(db))
            self.assertIn("visits", _columns(db))
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT isin, issuer, bucket, product_ref, visits "
                    "FROM isin_registry"
                ).fetchone()
            self.assertEqual(
                row,
                (
                    "IE00BKM4GZ66",
                    "ishares",
                    "equity_portfolio",
                    "264659",
                    0,
                ),
            )

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            _old_db(db)
            self.assertTrue(add_visits_column(db))
            self.assertFalse(add_visits_column(db))
            self.assertIn("visits", _columns(db))

    def test_dry_run_does_not_change_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            _old_db(db)
            self.assertTrue(add_visits_column(db, dry_run=True))
            self.assertNotIn("visits", _columns(db))

    def test_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                add_visits_column(Path(tmp) / "missing.db")

    def test_missing_registry_table_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE other (key TEXT PRIMARY KEY)")
            with self.assertRaisesRegex(ValueError, "isin_registry"):
                add_visits_column(db)


if __name__ == "__main__":
    unittest.main()
