# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user accounts store: rows, repository and backend wiring.

The accounts live in their own repository (``users.json``), keyed by
user name: the key is the primary key a database table would carry,
the payload is the password. Passwords may be empty.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from storage.records import UserRecord
from storage.repositories import UsersRepository
from storage.wiring import (
    BACKEND_JSON,
    BACKEND_POSTGRES,
    POSTGRES_USERS_TABLE,
    users_backend,
)


class TestUserRecord(unittest.TestCase):
    def test_password_only_payload(self) -> None:
        record = UserRecord("default", {"password": "foobar"})
        self.assertEqual(record.to_dict(), {"password": "foobar"})
        self.assertEqual(record.user, "default")
        self.assertEqual(record.password, "foobar")

    def test_empty_password_is_allowed(self) -> None:
        record = UserRecord("default", {"password": ""})
        self.assertEqual(record.password, "")
        self.assertEqual(record.to_dict(), {"password": ""})

    def test_missing_password_reads_as_empty(self) -> None:
        record = UserRecord("default")
        self.assertEqual(record.password, "")
        self.assertEqual(record.to_dict(), {"password": ""})

    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            UserRecord("default", {"password": "", "role": "admin"})

    def test_non_string_password_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            UserRecord("default", {"password": 42})

    def test_empty_user_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            UserRecord("  ", {"password": ""})


class TestUsersRepository(unittest.TestCase):
    def _repo(self, path: Path) -> UsersRepository:
        return UsersRepository(users_backend(path)).open()

    def test_reads_the_users_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"default": {"password": ""}, "alice": {"password": "x"}}),
                encoding="utf-8",
            )
            repo = self._repo(path)
            self.assertTrue(repo.exists("default"))
            self.assertTrue(repo.exists("alice"))
            self.assertFalse(repo.exists("nobody"))
            self.assertIn("alice", repo)
            self.assertEqual(repo.get("alice").password, "x")
            self.assertEqual(repo.get("nobody"), None)
            self.assertEqual(repo.list_users(), ["alice", "default"])

    def test_missing_file_is_an_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp) / "nope.json")
            self.assertFalse(repo.exists("default"))
            self.assertEqual(repo.list_users(), [])

    def test_add_user_writes_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            repo = self._repo(path)
            repo.add_user("default", "")
            repo.persist()
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"default": {"password": ""}},
            )

    def test_add_user_keeps_an_existing_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"default": {"password": "keepme"}}), encoding="utf-8"
            )
            repo = self._repo(path)
            repo.add_user("default", "other")
            repo.persist()
            self.assertEqual(repo.get("default").password, "keepme")

    def test_set_password_updates_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            repo = self._repo(path)
            repo.set_password("default", "new")
            repo.set_password("alice", "")
            repo.persist()
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["default"]["password"], "new")
            self.assertEqual(stored["alice"]["password"], "")

    def test_remove_user_drops_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text(
                json.dumps({"default": {"password": ""}}), encoding="utf-8"
            )
            repo = self._repo(path)
            self.assertTrue(repo.remove_user("default"))
            self.assertFalse(repo.remove_user("default"))
            repo.persist()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {})


class TestUsersBackendWiring(unittest.TestCase):
    def test_json_backend_reads_the_configured_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            backend = users_backend(path, BACKEND_JSON)
            self.assertEqual(backend.path, path)

    def test_postgres_backend_uses_the_users_table(self) -> None:
        backend = users_backend("users.json", BACKEND_POSTGRES)
        self.assertEqual(backend.table, POSTGRES_USERS_TABLE)


if __name__ == "__main__":
    unittest.main()
