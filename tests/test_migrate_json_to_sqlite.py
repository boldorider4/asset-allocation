# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-shot import of the JSON stores into the SQLite database.

The script is the only place that knows the old JSON layout; the
application never sees it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "migrate_json_to_sqlite.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _seed(tmp: Path) -> None:
    (tmp / "assets.json").write_text(
        json.dumps(
            {
                "alice": {
                    "portfolio_name": "Fund",
                    "positions": {"equity_portfolio": []},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp / "cache.json").write_text(
        json.dumps({"IE00A": {"price": 1.5}}), encoding="utf-8"
    )
    (tmp / "isin.json").write_text(
        json.dumps(
            {"IE00A": {"issuer": "justetf", "bucket": "equity_portfolio", "product_ref": None}}
        ),
        encoding="utf-8",
    )
    (tmp / "users.json").write_text(
        json.dumps({"alice": {"password": "x"}}), encoding="utf-8"
    )


class TestMigrationScript(unittest.TestCase):
    def test_imports_every_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            db_file = root / "asalloc.db"
            result = _run("--db-file", str(db_file), "--source-dir", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(db_file.is_file())

            import sqlite3

            conn = sqlite3.connect(db_file)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(
                tables,
                {"user_assets", "cache", "isin_registry", "users", "sessions"},
            )
            self.assertEqual(
                conn.execute("SELECT payload FROM user_assets").fetchone()[0],
                '{"portfolio_name": "Fund", "positions": {"equity_portfolio": []}}',
            )
            self.assertEqual(
                conn.execute("SELECT payload FROM cache").fetchone()[0],
                '{"price": 1.5}',
            )
            self.assertEqual(
                conn.execute("SELECT payload FROM users").fetchone()[0],
                '{"password": "x"}',
            )

    def test_the_json_files_are_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            before = {
                name: (root / name).read_text(encoding="utf-8")
                for name in ("assets.json", "cache.json", "isin.json", "users.json")
            }
            result = _run(
                "--db-file", str(root / "asalloc.db"), "--source-dir", str(root)
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for name, text in before.items():
                self.assertEqual(
                    (root / name).read_text(encoding="utf-8"), text, name
                )

    def test_it_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            db_file = root / "asalloc.db"
            self.assertEqual(
                _run("--db-file", str(db_file), "--source-dir", str(root)).returncode, 0
            )
            result = _run("--db-file", str(db_file), "--source-dir", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)

            import sqlite3

            conn = sqlite3.connect(db_file)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM user_assets").fetchone()[0], 1
            )

    def test_a_broken_row_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets.json").write_text(
                json.dumps(
                    {
                        "alice": {"portfolio_name": "Fund", "positions": {}},
                        "broken": {"nope": 1},
                    }
                ),
                encoding="utf-8",
            )
            db_file = root / "asalloc.db"
            result = _run("--db-file", str(db_file), "--source-dir", str(root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skipping bad row", result.stdout)

            import sqlite3

            conn = sqlite3.connect(db_file)
            self.assertEqual(
                conn.execute("SELECT key FROM user_assets").fetchall(), [("alice",)]
            )

    def test_a_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed(root)
            db_file = root / "asalloc.db"
            result = _run(
                "--db-file", str(db_file), "--source-dir", str(root), "--dry-run"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry run", result.stdout)
            self.assertFalse(db_file.exists())


if __name__ == "__main__":
    unittest.main()
