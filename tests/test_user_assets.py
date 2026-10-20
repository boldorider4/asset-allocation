# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user-keyed assets store: shape, isolation, migration, wiring.

The store is reached through ``storage.repositories.AssetRepository``
over the backend chosen in ``storage.wiring`` — the same path
production uses, so these tests exercise the real composition.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.common import DEFAULT_USER_NAME  # noqa: E402
from cli.context import AppConfig, RuntimeContext  # noqa: E402
from storage.json_storage import JsonStorage  # noqa: E402
from storage.postgres_storage import PostgresStorage  # noqa: E402
from storage.records import UserAssets  # noqa: E402
from storage.repositories import AssetRepository, UnknownUser  # noqa: E402
from storage.wiring import (  # noqa: E402
    BACKEND_JSON,
    BACKEND_POSTGRES,
    DEFAULT_POSTGRES_DSN,
    assets_backend,
    normalize_backend,
)

BUCKET = "equity_portfolio"
POSITION = {"name": "ETF", "ISIN": "IE00TEST0001", "shares": 2, "value": 100.0}


def _repo(path: Path, user: str) -> AssetRepository:
    return AssetRepository(user, assets_backend(path, "json"))


class TestAssetsStoreShape(unittest.TestCase):
    def test_store_is_keyed_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            repo = _repo(path, "foobar")
            repo.open()
            repo.restore({BUCKET: [POSITION]})
            repo.persist()
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(raw), ["foobar"])
            self.assertEqual(raw["foobar"][BUCKET][0]["ISIN"], "IE00TEST0001")

    def test_legacy_flat_store_is_rejected_with_migration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({BUCKET: [POSITION]}), encoding="utf-8")
            repo = _repo(path, DEFAULT_USER_NAME)
            with self.assertRaises(ValueError) as ctx:
                repo.open()
            self.assertIn("migrate_assets_user.py", str(ctx.exception))

    def test_malformed_store_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({"default": "nope"}), encoding="utf-8")
            repo = _repo(path, DEFAULT_USER_NAME)
            with self.assertRaises((TypeError, ValueError)):
                repo.open()

    def test_unknown_user_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp) / "assets.json", "alice")
            repo.open()
            self.assertFalse(repo.exists())
            with self.assertRaises(UnknownUser):
                repo.require()


class TestUserIsolation(unittest.TestCase):
    def test_writing_one_user_preserves_the_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            for user, value in (("alice", 100.0), ("bob", 1.0)):
                repo = _repo(path, user)
                repo.open()
                repo.restore({BUCKET: [dict(POSITION, value=value)]})
                repo.persist()
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(raw), ["alice", "bob"])
            self.assertEqual(raw["alice"][BUCKET][0]["value"], 100.0)
            self.assertEqual(raw["bob"][BUCKET][0]["value"], 1.0)

    def test_repository_is_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            backend = assets_backend(path, "json")
            alice = AssetRepository("alice", backend)
            bob = AssetRepository("bob", backend)
            alice.set_positions(BUCKET, [POSITION])
            self.assertEqual(alice.snapshot()[BUCKET][0]["ISIN"], "IE00TEST0001")
            self.assertEqual(bob.snapshot(), {})
            self.assertTrue(alice.exists())
            self.assertFalse(bob.exists())


class TestRuntimeContextUser(unittest.TestCase):
    def _ctx(self, tmp: Path, user: str) -> RuntimeContext:
        return RuntimeContext(
            config=AppConfig(
                user=user,
                assets_file=tmp / "assets.json",
                cache_file=tmp / "cache.json",
                isin_file=tmp / "isin.json",
            )
        )

    def test_load_and_persist_round_trip_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _repo(root / "assets.json", "alice")
            repo.open()
            repo.restore({BUCKET: [POSITION]})
            repo.persist()
            alice = self._ctx(root, "alice")
            alice.load_portfolio()
            self.assertEqual(alice.portfolio[BUCKET][0]["ISIN"], "IE00TEST0001")
            bob = self._ctx(root, "bob")
            bob.load_portfolio()
            self.assertEqual(bob.portfolio, {})
            # Writing bob's (empty) portfolio must not touch alice's.
            bob.persist_portfolio()
            raw = json.loads((root / "assets.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["alice"][BUCKET][0]["ISIN"], "IE00TEST0001")

    def test_default_user_name(self) -> None:
        self.assertEqual(AppConfig().user, DEFAULT_USER_NAME)


class TestBackendSelection(unittest.TestCase):
    """The assets backend is chosen by the app and built by storage.wiring."""

    def test_default_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as store:
            backend = assets_backend(Path(store) / "assets.json")
        self.assertIsInstance(backend, JsonStorage)

    def test_postgres_is_handed_over_and_logged(self) -> None:
        # The stub is wired in and an error is logged; the wiring itself
        # does not raise.
        with tempfile.TemporaryDirectory() as store:
            with self.assertLogs("storage.wiring", level="ERROR"):
                backend = assets_backend(
                    Path(store) / "assets.json",
                    BACKEND_POSTGRES,
                    "postgresql://example/db",
                )
        self.assertIsInstance(backend, PostgresStorage)
        self.assertEqual(backend.table, "user_assets")

    def test_unknown_backend_falls_back_to_json(self) -> None:
        with self.assertLogs("storage.wiring", level="WARNING"):
            self.assertEqual(normalize_backend("sqlite"), "json")
        with tempfile.TemporaryDirectory() as store:
            backend = assets_backend(
                Path(store) / "assets.json", "sqlite"
            )
        self.assertIsInstance(backend, JsonStorage)

    def test_wiring_reads_no_configuration(self) -> None:
        # The wiring module is a pure factory: the app resolves the
        # settings and passes them in.
        import inspect

        import storage.wiring as wiring

        self.assertFalse(hasattr(wiring, "config_path"))
        self.assertFalse(hasattr(wiring, "configured_backend"))
        self.assertFalse(hasattr(wiring, "configparser"))
        self.assertFalse(hasattr(wiring, "os"))
        params = inspect.signature(wiring.assets_backend).parameters
        self.assertIn("backend", params)
        self.assertIn("postgres_dsn", params)

    def test_server_config_carries_the_setting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\naddress = localhost\n"
                "directory = /tmp/x\nbackend = postgres\n"
                "postgres_dsn = postgresql://example/db\n",
                encoding="utf-8",
            )
            config = AppConfig.from_ini(ini)
        self.assertEqual(config.server.backend, "postgres")
        self.assertEqual(config.server.postgres_dsn, "postgresql://example/db")

    def test_server_config_defaults_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\naddress = localhost\n"
                "directory = /tmp/x\n",
                encoding="utf-8",
            )
            config = AppConfig.from_ini(ini)
        self.assertEqual(config.server.backend, "json")
        self.assertEqual(config.server.postgres_dsn, DEFAULT_POSTGRES_DSN)


class TestWiring(unittest.TestCase):
    def test_assets_backend_is_chosen_in_one_place(self) -> None:
        # The backend is composed by storage.wiring only: swapping in
        # Postgres later is a one-line change there.
        import storage.wiring as wiring

        with tempfile.TemporaryDirectory() as tmp:
            backend = wiring.assets_backend(Path(tmp) / "assets.json", "json")
        self.assertEqual(backend._factory, UserAssets)  # type: ignore[attr-defined]

    def test_no_json_shaped_helpers_in_the_storage_layer(self) -> None:
        # The storage layer must not know about file formats: the old
        # assets_file module (file IO + legacy detection) is gone, and
        # only the backends may touch a file format.
        self.assertFalse((REPO_ROOT / "storage" / "assets_file.py").exists())
        for name in ("records.py", "repositories.py", "storage.py", "wiring.py"):
            source = (REPO_ROOT / "storage" / name).read_text(encoding="utf-8")
            self.assertNotIn("json.dump", source, name)
            self.assertNotIn("json.load", source, name)
            self.assertNotIn("with open(", source, name)


class TestMigrationScript(unittest.TestCase):
    def test_migrates_legacy_file_and_keeps_backup(self) -> None:
        script = REPO_ROOT / "scripts" / "migrate_assets_user.py"
        self.assertTrue(script.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({BUCKET: [POSITION]}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), str(path), "foobar"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            repo = _repo(path, "foobar")
            repo.open()
            self.assertEqual(repo.snapshot()[BUCKET][0]["ISIN"], "IE00TEST0001")
            backup = path.with_name(path.name + ".bak")
            self.assertTrue(backup.is_file())
            # The backup still holds the original flat file.
            self.assertEqual(
                list(json.loads(backup.read_text(encoding="utf-8"))), [BUCKET]
            )

    def test_refuses_non_legacy_file(self) -> None:
        script = REPO_ROOT / "scripts" / "migrate_assets_user.py"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            repo = _repo(path, "default")
            repo.open()
            repo.restore({BUCKET: [POSITION]})
            repo.persist()
            result = subprocess.run(
                [sys.executable, str(script), str(path), "foobar"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("legacy", result.stderr)
            # Untouched.
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(raw), ["default"])


if __name__ == "__main__":
    unittest.main()
