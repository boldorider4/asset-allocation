# SPDX-License-Identifier: AGPL-3.0-or-later
"""The user-keyed assets file: shape, isolation, migration, server routing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.context import AppConfig, RuntimeContext  # noqa: E402
from storage.assets_file import (  # noqa: E402
    DEFAULT_USER_NAME,
    LegacyAssetsFormat,
    UnknownUser,
    is_legacy_root,
    read_assets_root,
    require_user,
    user_buckets,
    write_assets_root,
)
from storage.json_storage import JsonStorage  # noqa: E402
from storage.records import UserAssets  # noqa: E402
from storage.repositories import AssetRepository  # noqa: E402
from utils import load_portfolio, write_portfolio  # noqa: E402

BUCKET = "equity_portfolio"
POSITION = {"name": "ETF", "ISIN": "IE00TEST0001", "shares": 2, "value": 100.0}


class TestAssetsFileShape(unittest.TestCase):
    def test_file_is_keyed_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            write_assets_root(path, {"foobar": {BUCKET: [POSITION]}})
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(list(raw), ["foobar"])
            self.assertEqual(raw["foobar"][BUCKET][0]["ISIN"], "IE00TEST0001")

    def test_legacy_flat_file_is_detected(self) -> None:
        self.assertTrue(is_legacy_root({BUCKET: [POSITION]}))
        self.assertFalse(is_legacy_root({"default": {BUCKET: [POSITION]}}))
        self.assertFalse(is_legacy_root({}))

    def test_legacy_file_is_rejected_with_migration_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            path.write_text(json.dumps({BUCKET: [POSITION]}), encoding="utf-8")
            with self.assertRaises(LegacyAssetsFormat) as ctx:
                read_assets_root(path)
            self.assertIn("migrate_assets_user.py", str(ctx.exception))

    def test_unknown_user_raises(self) -> None:
        root = {"default": {BUCKET: [POSITION]}}
        self.assertEqual(require_user(root, "default"), {BUCKET: [POSITION]})
        with self.assertRaises(UnknownUser):
            require_user(root, "alice")
        self.assertEqual(user_buckets(root, "alice"), {})


class TestUserIsolation(unittest.TestCase):
    def test_write_portfolio_preserves_other_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            write_portfolio(path, {BUCKET: [POSITION]}, "alice")
            write_portfolio(path, {BUCKET: [dict(POSITION, value=1.0)]}, "bob")
            root = read_assets_root(path)
            self.assertEqual(sorted(root), ["alice", "bob"])
            self.assertEqual(root["alice"][BUCKET][0]["value"], 100.0)
            self.assertEqual(root["bob"][BUCKET][0]["value"], 1.0)

    def test_load_portfolio_scopes_to_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            write_portfolio(path, {BUCKET: [POSITION]}, "alice")
            self.assertEqual(load_portfolio(path, "alice")[BUCKET][0]["ISIN"], "IE00TEST0001")
            self.assertEqual(load_portfolio(path, "bob"), {})

    def test_repository_is_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assets.json"
            backend = JsonStorage(path, UserAssets)
            alice = AssetRepository("alice", backend)
            bob = AssetRepository("bob", backend)
            alice.set_positions(BUCKET, [POSITION])
            self.assertEqual(alice.snapshot()[BUCKET][0]["ISIN"], "IE00TEST0001")
            self.assertEqual(bob.snapshot(), {})
            backend.persist()
            self.assertEqual(sorted(read_assets_root(path)), ["alice"])


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
            write_assets_root(root / "assets.json", {"alice": {BUCKET: [POSITION]}})
            alice = self._ctx(root, "alice")
            alice.load_portfolio()
            self.assertEqual(alice.portfolio[BUCKET][0]["ISIN"], "IE00TEST0001")
            bob = self._ctx(root, "bob")
            bob.load_portfolio()
            self.assertEqual(bob.portfolio, {})
            # Writing bob's (empty) portfolio must not touch alice's.
            bob.persist_portfolio()
            self.assertEqual(
                read_assets_root(root / "assets.json")["alice"][BUCKET][0]["ISIN"],
                "IE00TEST0001",
            )

    def test_default_user_name(self) -> None:
        self.assertEqual(AppConfig().user, DEFAULT_USER_NAME)


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
            root = read_assets_root(path)
            self.assertEqual(list(root), ["foobar"])
            self.assertEqual(root["foobar"][BUCKET][0]["ISIN"], "IE00TEST0001")
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
            write_assets_root(path, {"default": {BUCKET: [POSITION]}})
            result = subprocess.run(
                [sys.executable, str(script), str(path), "foobar"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("legacy", result.stderr)
            # Untouched.
            self.assertEqual(list(read_assets_root(path)), ["default"])


if __name__ == "__main__":
    unittest.main()
