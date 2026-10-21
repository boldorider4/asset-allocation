# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adding a user account: the script and the ``asalloc add-user`` command.

Both write to the same users store (``users.json``), keyed by user
name, and both refuse to work against Postgres (a stub for now).
"""

from __future__ import annotations

import json
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


class TestAddUsersScript(unittest.TestCase):
    def test_adds_an_account_with_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            result = _run_script(str(path), "alice", "--password", "secret")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"alice": {"password": "secret"}},
            )

    def test_adds_an_account_without_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            result = _run_script(str(path), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"alice": {"password": ""}},
            )

    def test_keeps_the_other_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"default": {"password": "keep"}}), encoding="utf-8"
            )
            result = _run_script(str(path), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"default": {"password": "keep"}, "alice": {"password": ""}},
            )

    def test_existing_account_keeps_its_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"alice": {"password": "keep"}}), encoding="utf-8"
            )
            result = _run_script(str(path), "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already exists", result.stdout)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"alice": {"password": "keep"}},
            )

    def test_existing_account_gets_a_new_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"alice": {"password": "keep"}}), encoding="utf-8"
            )
            result = _run_script(str(path), "alice", "--password", "new")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"alice": {"password": "new"}},
            )

    def test_empty_user_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            result = _run_script(str(path), "   ")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(path.exists())

    def test_postgres_is_a_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            result = _run_script(str(path), "alice", "--backend", "postgres")
            self.assertEqual(result.returncode, 1)
            self.assertIn("not implemented", result.stderr)
            self.assertFalse(path.exists())


class TestAddUserCommand(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cli.cli", "add-user", *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def _ini(self, tmp: str) -> tuple[Path, Path]:
        """An ini pointing at a users file; returns (ini, users file)."""
        users = Path(tmp) / "users.json"
        ini = Path(tmp) / "config.ini"
        ini.write_text(
            "[server]\nport = 8765\ndirectory = vis\n"
            f"[update]\nusers_file = {users}\n",
            encoding="utf-8",
        )
        return ini, users

    def test_adds_an_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, users = self._ini(tmp)
            result = self._run(
                "--config", str(ini), "--user", "alice", "--password", "secret"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(users.read_text(encoding="utf-8")),
                {"alice": {"password": "secret"}},
            )

    def test_adds_an_account_without_a_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, users = self._ini(tmp)
            result = self._run("--config", str(ini), "--user", "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(users.read_text(encoding="utf-8")),
                {"alice": {"password": ""}},
            )

    def test_the_users_file_comes_from_the_ini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini, users = self._ini(tmp)
            result = self._run("--config", str(ini), "--user", "alice")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(users.is_file())

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
