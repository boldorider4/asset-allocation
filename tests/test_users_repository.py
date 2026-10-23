# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user accounts store: rows, repository and backend wiring.

The accounts live in the ``users`` table of the SQLite database, keyed
by user name: the key is the primary key, the payload is the password.
Passwords may be empty.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.records import UserRecord
from storage.repositories import UsersRepository
from storage.wiring import SQLITE_TABLE_USERS, users_backend


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

    def test_reads_the_users_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            repo = self._repo(db)
            repo.add_user("default", "")
            repo.add_user("alice", "x")
            repo.persist()
            repo.close()

            repo = self._repo(db)
            self.assertTrue(repo.exists("default"))
            self.assertTrue(repo.exists("alice"))
            self.assertFalse(repo.exists("nobody"))
            self.assertIn("alice", repo)
            self.assertEqual(repo.get("alice").password, "x")
            self.assertEqual(repo.get("nobody"), None)
            self.assertEqual(repo.list_users(), ["alice", "default"])

    def test_missing_database_is_an_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp) / "nope.db")
            self.assertFalse(repo.exists("default"))
            self.assertEqual(repo.list_users(), [])

    def test_add_user_writes_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            repo = self._repo(db)
            repo.add_user("default", "")
            repo.persist()
            repo.close()

            repo = self._repo(db)
            self.assertEqual(repo.get("default").password, "")

    def test_add_user_keeps_an_existing_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            repo = self._repo(db)
            repo.add_user("default", "keepme")
            repo.persist()
            repo.add_user("default", "other")
            repo.persist()
            self.assertEqual(repo.get("default").password, "keepme")

    def test_set_password_updates_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            repo = self._repo(db)
            repo.set_password("default", "new")
            repo.set_password("alice", "")
            repo.persist()
            self.assertEqual(repo.get("default").password, "new")
            self.assertEqual(repo.get("alice").password, "")

    def test_remove_user_drops_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "asalloc.db"
            repo = self._repo(db)
            repo.add_user("default", "")
            repo.persist()
            self.assertTrue(repo.remove_user("default"))
            self.assertFalse(repo.remove_user("default"))
            repo.persist()
            self.assertEqual(repo.list_users(), [])


class TestCheckCredentials(unittest.TestCase):
    def _repo(self, path: Path) -> UsersRepository:
        return UsersRepository(users_backend(path)).open()

    def test_the_right_password_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp) / "asalloc.db")
            repo.add_user("alice", "s3cret")
            repo.persist()
            self.assertTrue(repo.check_credentials("alice", "s3cret"))
            self.assertFalse(repo.check_credentials("alice", "wrong"))
            self.assertFalse(repo.check_credentials("alice", ""))
            self.assertFalse(repo.check_credentials("bob", "s3cret"))

    def test_an_empty_password_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp) / "asalloc.db")
            repo.add_user("alice", "")
            repo.persist()
            self.assertTrue(repo.check_credentials("alice", ""))
            self.assertFalse(repo.check_credentials("alice", "something"))

    def test_a_non_ascii_password_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp) / "asalloc.db")
            repo.add_user("alice", "pässwörd€")
            repo.persist()
            self.assertTrue(repo.check_credentials("alice", "pässwörd€"))
            self.assertFalse(repo.check_credentials("alice", "passwörd"))


class TestUsersBackendWiring(unittest.TestCase):
    def test_sqlite_backend_uses_the_users_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            backend = users_backend(db)
            self.assertEqual(backend.table, SQLITE_TABLE_USERS)


if __name__ == "__main__":
    unittest.main()
