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
from storage.records import UserAssets  # noqa: E402
from storage.repositories import AssetRepository, UnknownUser  # noqa: E402
from storage.wiring import (  # noqa: E402
    BACKEND_JSON,
    BACKEND_SQLITE,
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
            self.assertEqual(
                raw["foobar"]["portfolio_name"], "Portfolio"
            )
            self.assertEqual(
                raw["foobar"]["positions"][BUCKET][0]["ISIN"], "IE00TEST0001"
            )

    def test_legacy_flat_store_is_rejected_with_migration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({BUCKET: [POSITION]}), encoding="utf-8")
            repo = _repo(path, DEFAULT_USER_NAME)
            with self.assertRaises(ValueError) as ctx:
                repo.open()
            self.assertIn("migrate_assets_user.py", str(ctx.exception))

    def test_old_bucket_payload_is_rejected(self) -> None:
        # The positions used to sit directly under the user; that shape
        # is not the current one and fails loudly.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(
                json.dumps({"default": {BUCKET: [POSITION]}}), encoding="utf-8"
            )
            repo = _repo(path, DEFAULT_USER_NAME)
            with self.assertRaises((KeyError, TypeError, ValueError)):
                repo.open()

    def test_portfolio_name_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            repo = _repo(path, "foobar")
            repo.open()
            self.assertEqual(repo.portfolio_name, "Portfolio")
            repo.set_portfolio_name("My Portfolio")
            self.assertEqual(repo.portfolio_name, "My Portfolio")
            repo.restore({BUCKET: [POSITION]}, portfolio_name=repo.portfolio_name)
            repo.persist()
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["foobar"]["portfolio_name"], "My Portfolio")
            # The name rides along with the positions.
            self.assertEqual(raw["foobar"]["positions"][BUCKET][0]["ISIN"], "IE00TEST0001")

    def test_restore_keeps_the_portfolio_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo(Path(tmp) / "assets.json", "foobar")
            repo.open()
            repo.set_portfolio_name("Kept")
            repo.restore({BUCKET: [POSITION]})
            self.assertEqual(repo.portfolio_name, "Kept")


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
            self.assertEqual(raw["alice"]["positions"][BUCKET][0]["value"], 100.0)
            self.assertEqual(raw["bob"]["positions"][BUCKET][0]["value"], 1.0)

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
            self.assertEqual(
                raw["alice"]["positions"][BUCKET][0]["ISIN"], "IE00TEST0001"
            )

    def test_default_user_name(self) -> None:
        self.assertEqual(AppConfig().user, DEFAULT_USER_NAME)


class TestBackendSelection(unittest.TestCase):
    """The assets backend is chosen by the app and built by storage.wiring."""

    def test_default_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as store:
            backend = assets_backend(Path(store) / "assets.json")
        self.assertIsInstance(backend, JsonStorage)

    def test_unknown_backend_falls_back_to_json(self) -> None:
        with self.assertLogs("storage.wiring", level="WARNING"):
            self.assertEqual(normalize_backend("mongodb"), "json")
        with tempfile.TemporaryDirectory() as store:
            backend = assets_backend(
                Path(store) / "assets.json", "mongodb"
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
        self.assertFalse(hasattr(config.server, "postgres_dsn"))

    def test_server_config_reads_db_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "config.ini"
            ini.write_text(
                "[server]\nport = 8765\naddress = localhost\n"
                "directory = /tmp/x\nbackend = sqlite\n"
                "db_file = /tmp/test.db\n",
                encoding="utf-8",
            )
            config = AppConfig.from_ini(ini)
        self.assertEqual(config.server.backend, "sqlite")
        self.assertEqual(config.server.db_file, Path("/tmp/test.db"))


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
            # The flat file becomes a user-keyed one...
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(raw), ["foobar"])
            self.assertEqual(raw["foobar"][BUCKET][0]["ISIN"], "IE00TEST0001")
            backup = path.with_name(path.name + ".bak")
            self.assertTrue(backup.is_file())
            # The backup still holds the original flat file.
            self.assertEqual(
                list(json.loads(backup.read_text(encoding="utf-8"))), [BUCKET]
            )


class TestPositionsMigrationScript(unittest.TestCase):
    """The current format: a named portfolio with nested positions."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        script = REPO_ROOT / "scripts" / "migrate_assets_positions.py"
        self.assertTrue(script.is_file())
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
        )

    def test_migrates_the_positions_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(
                json.dumps({"foobar": {BUCKET: [POSITION]}}), encoding="utf-8"
            )
            result = self._run(str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["foobar"]["portfolio_name"], "Portfolio")
            self.assertEqual(
                raw["foobar"]["positions"][BUCKET][0]["ISIN"], "IE00TEST0001"
            )
            # The migrated file loads through the repository.
            repo = _repo(path, "foobar")
            repo.open()
            self.assertEqual(repo.snapshot()[BUCKET][0]["ISIN"], "IE00TEST0001")
            self.assertEqual(repo.portfolio_name, "Portfolio")
            backup = path.with_name(path.name + ".bak")
            self.assertTrue(backup.is_file())
            self.assertEqual(
                list(json.loads(backup.read_text(encoding="utf-8"))), ["foobar"]
            )

    def test_names_the_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(
                json.dumps({"foobar": {BUCKET: [POSITION]}}), encoding="utf-8"
            )
            result = self._run(str(path), "--portfolio-name", "90-10 Equity")
            self.assertEqual(result.returncode, 0, result.stderr)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["foobar"]["portfolio_name"], "90-10 Equity")

    def test_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(
                json.dumps({"foobar": {BUCKET: [POSITION]}}), encoding="utf-8"
            )
            self.assertEqual(self._run(str(path)).returncode, 0)
            before = path.read_text(encoding="utf-8")
            self.assertEqual(self._run(str(path)).returncode, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_migrates_a_flat_file_in_two_steps(self) -> None:
        # A pre-user file needs both scripts: the first makes it
        # user-keyed, the second moves the positions under their name.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({BUCKET: [POSITION]}), encoding="utf-8")
            first = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "migrate_assets_user.py"),
                    str(path),
                    "foobar",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(self._run(str(path)).returncode, 0)
            repo = _repo(path, "foobar")
            repo.open()
            self.assertEqual(repo.snapshot()[BUCKET][0]["ISIN"], "IE00TEST0001")

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
