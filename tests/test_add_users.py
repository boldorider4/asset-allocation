# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adding a user account: the script and the ``asalloc add-user`` command.

Both write to the ``users`` table of the SQLite database and ensure a
matching ``user_portfolios`` row.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "add_users.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _users(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return {
            str(name): str(password)
            for name, password in conn.execute(
                "SELECT username, password FROM users"
            )
        }
    finally:
        conn.close()


class TestAddUsersScript(unittest.TestCase):
    def test_adds_an_account_with_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            result = _run_script(str(db), "alice", "--password", "secret")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"alice": "secret"})

    def test_adds_an_account_without_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            result = _run_script(str(db), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"alice": ""})

    def test_keeps_the_other_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            self.assertEqual(_run_script(str(db), "default", "--password", "keep").returncode, 0)
            result = _run_script(str(db), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"default": "keep", "alice": ""})

    def test_account_gets_a_portfolio_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            result = _run_script(str(db), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(
                    conn.execute("SELECT username FROM user_portfolios").fetchall(),
                    [("alice",)],
                )
            finally:
                conn.close()

    def test_existing_account_keeps_its_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            _run_script(str(db), "alice", "--password", "keep")
            result = _run_script(str(db), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already exists", result.stdout)
            self.assertEqual(_users(db), {"alice": "keep"})

    def test_existing_account_gets_a_new_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            _run_script(str(db), "alice", "--password", "keep")
            result = _run_script(str(db), "alice", "--password", "new")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"alice": "new"})

    def test_empty_user_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            result = _run_script(str(db), "   ")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(db.exists())


class TestAddUserCommand(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cli.cli", "add-user", *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def _ini(self, tmp: str) -> tuple[Path, Path]:
        """An ini pointing at a database; returns (ini, db)."""
        db = Path(tmp) / "asalloc.db"
        ini = Path(tmp) / "config.ini"
        ini.write_text(
            "[server]\nport = 8765\ndirectory = vis\n"
            f"db_file = {db}\n",
            encoding="utf-8",
        )
        return ini, db

    def test_adds_an_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, db = self._ini(tmp)
            result = self._run(
                "--config", str(ini), "--user", "alice", "--password", "secret"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"alice": "secret"})

    def test_adds_an_account_without_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, db = self._ini(tmp)
            result = self._run("--config", str(ini), "--user", "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_users(db), {"alice": ""})

    def test_account_gets_a_portfolio_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, db = self._ini(tmp)
            result = self._run("--config", str(ini), "--user", "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(
                    conn.execute("SELECT username FROM user_portfolios").fetchall(),
                    [("alice",)],
                )
            finally:
                conn.close()

    def test_the_database_comes_from_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, db = self._ini(tmp)
            result = self._run("--config", str(ini), "--user", "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(db.is_file())

    def test_needs_a_config(self) -> None:
        result = self._run("--user", "alice")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--config", result.stderr)

    def test_needs_a_user_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, _ = self._ini(tmp)
            result = self._run("--config", str(ini))
            self.assertEqual(result.returncode, 2)
            self.assertIn("--user", result.stderr)

    def test_an_unreadable_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                "--config", str(Path(tmp) / "nope.ini"), "--user", "alice"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("error", result.stderr)


if __name__ == "__main__":
    unittest.main()
