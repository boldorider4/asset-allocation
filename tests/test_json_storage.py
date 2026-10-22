# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the standalone ``storage`` package (no wiring to the app yet).

Covers row types (``storage.records``), the JSON and in-memory backends,
the Postgres stub, and — most importantly — repository composition:
the same domain assertions run unchanged against both real backends.

Run from repo root::

    pytest tests/test_json_storage.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage import (  # noqa: E402
    AssetBucket,
    AssetRepository,
    CacheEntry,
    CacheRepository,
    DictRow,
    IsinRecord,
    IsinRegistry,
    JsonStorage,
    MemoryStorage,
    PositionRecord,
    PositionRepository,
    UserPortfolioRecord,
    UserPortfolioRepository,
)
from storage.sqlite_storage import SqliteStorage  # noqa: E402
from storage.storage import Storage  # noqa: E402


class TestDictRow(unittest.TestCase):
    def test_bare_json_storage_defaults_to_dict_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scratch.json"
            store = JsonStorage(path)
            store.put(DictRow("k", {}))
            store.save()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved, {"k": {}})
            reloaded = JsonStorage(path)
            row = reloaded.get("k")
            self.assertIsInstance(row, DictRow)

    def test_explicit_memory_storage_with_dict_row(self) -> None:
        store = MemoryStorage(DictRow)
        store.upsert("k", {})
        row = store.get("k")
        self.assertIsInstance(row, DictRow)
        self.assertEqual(row.to_dict(), {})

    def test_merge_validates_and_rolls_back(self) -> None:
        entry = CacheEntry("IE00X", {"price": 10.0})
        with self.assertRaises(ValueError):
            entry.merge({"price": -1.0})
        self.assertEqual(entry.price, 10.0)  # failed merge left state intact

    def test_unknown_key_raises(self) -> None:
        entry = CacheEntry("IE00X", {"price": 1.0})
        with self.assertRaises(KeyError):
            entry.merge({"bogus": 1})
        with self.assertRaises(KeyError):
            CacheEntry.from_dict("IE00X", {"bogus": 1})

    def test_bad_values_raise(self) -> None:
        with self.assertRaises(ValueError):
            CacheEntry("IE00X", {"price": -1.0})
        with self.assertRaises(ValueError):
            CacheEntry("IE00X", {"countries": {"France": 1.5}})
        with self.assertRaises((TypeError, ValueError)):
            CacheEntry.from_dict("IE00X", {"price": "abc"})
        with self.assertRaises(TypeError):
            CacheEntry.from_dict("IE00X", ["not", "a", "dict"])

    def test_out_of_range_weights_rejected(self) -> None:
        # Strict: split rows are normalized at Position assembly (see
        # ``position.normalize_split_rows``), so anything outside [0, 1]
        # here is a genuine error — including near misses from source
        # rounding, which must never reach the store unnormalized.
        with self.assertRaises(ValueError):
            CacheEntry(
                "IE00BFNM3L97", {"countries": {"Japan": 1.0003000000000006}}
            )
        with self.assertRaises(ValueError):
            CacheEntry("IE00X", {"sectors": {"Tech": -0.0002}})
        with self.assertRaises(ValueError):
            CacheEntry("IE00X", {"countries": {"France": 1.5}})
        with self.assertRaises(ValueError):
            CacheEntry("IE00X", {"countries": {"France": -0.5}})

    def test_row_converters_and_parsed(self) -> None:
        self.assertEqual(
            CacheEntry.rows_to_fractions([{"name": "France", "weight_pct": 50.0}]),
            {"France": 0.5},
        )
        self.assertEqual(
            CacheEntry.fractions_to_rows({"France": 0.5}),
            [{"name": "France", "weight_pct": 50.0}],
        )
        self.assertIsNone(CacheEntry.fractions_to_rows(None))
        entry = CacheEntry(
            "IE00X", {"price": 2.0, "countries": {"A": 1.0}, "sectors": {"B": 1.0}}
        )
        self.assertEqual(entry.parsed(), (2.0, {"A": 1.0}, {"B": 1.0}))

    def test_bad_issuer_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IsinRecord("IE00X", {"issuer": "nope"})

    def test_blank_key_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IsinRecord("   ", {"issuer": "dws"})

    def test_nullable_fields_accepted(self) -> None:
        record = IsinRecord("IE00X", {})
        self.assertIsNone(record.issuer)
        self.assertIsNone(record.bucket)
        self.assertIsNone(record.product_ref)
        record = IsinRecord(
            "IE00Y",
            {"issuer": "dws", "bucket": "equity_portfolio", "product_ref": None},
        )
        self.assertEqual(record.issuer, "dws")
        self.assertEqual(record.bucket, "equity_portfolio")


class TestJsonStorageBackend(unittest.TestCase):
    def test_upsert_round_trips_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            store: Storage[CacheEntry] = JsonStorage(path, CacheEntry)
            store.upsert("IE00X", {"price": 12.5})
            store.upsert("IE00X", {"countries": {"France": 0.5}})
            assert isinstance(store, JsonStorage)
            self.assertTrue(store.is_dirty)
            store.save()
            self.assertFalse(store.is_dirty)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE00X"]["price"], 12.5)
        self.assertEqual(saved["IE00X"]["countries"], {"France": 0.5})

    def test_save_is_noop_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            store = JsonStorage(path, CacheEntry)
            store.load()
            store.save()
        self.assertFalse(path.exists())

    def test_missing_and_corrupt_files_start_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(JsonStorage(Path(tmp) / "nope.json", CacheEntry)), 0)
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(len(JsonStorage(bad, CacheEntry)), 0)
            wrong_root = Path(tmp) / "list.json"
            wrong_root.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(len(JsonStorage(wrong_root, CacheEntry)), 0)

    def test_bad_row_skipped_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text(
                json.dumps({"GOOD": {"price": 1.0}, "BAD": {"price": -5.0}}),
                encoding="utf-8",
            )
            store = JsonStorage(path, CacheEntry)
            self.assertIn("GOOD", store)
            self.assertNotIn("BAD", store)

    def test_add_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStorage(Path(tmp) / "c.json", CacheEntry)
            store.add(CacheEntry("IE00X", {"price": 1.0}))
            with self.assertRaises(KeyError):
                store.add(CacheEntry("IE00X", {"price": 2.0}))

    def test_asset_buckets_live_in_their_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            assets_path = Path(tmp) / "assets.json"
            assets = JsonStorage(assets_path, AssetBucket)
            assets.put(AssetBucket("equity_portfolio", [{"ISIN": "X", "value": 1.0}]))
            assets.save()
            self.assertFalse(cache_path.exists())
            saved = json.loads(assets_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["equity_portfolio"], [{"ISIN": "X", "value": 1.0}])

    def test_asset_bucket_validation(self) -> None:
        with self.assertRaises(TypeError):
            AssetBucket("b", ["not-a-dict"])  # type: ignore[list-item]


class TestMemoryStorage(unittest.TestCase):
    def test_point_access(self) -> None:
        store: Storage[CacheEntry] = MemoryStorage(CacheEntry)
        self.assertNotIn("IE00X", store)
        store.upsert("IE00X", {"price": 3.0})
        self.assertIn("IE00X", store)
        entry = store.get("IE00X")
        assert entry is not None
        self.assertEqual(entry.price, 3.0)
        with self.assertRaises(KeyError):
            store.add(CacheEntry("IE00X", {"price": 1.0}))
        store.remove("IE00X")
        self.assertNotIn("IE00X", store)
        store.remove("IE00X")  # no error when missing
        store.close()  # safe no-op

    def test_strict_initial(self) -> None:
        with self.assertRaises(ValueError):
            MemoryStorage(CacheEntry, {"BAD": {"price": -1.0}})


class TestLifecycleVerbs(unittest.TestCase):
    """Backend-neutral ``open``/``snapshot``/``persist``/``close`` on every backend."""

    def test_open_snapshot_persist_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backends: list[tuple[str, Storage[CacheEntry]]] = [
                ("json", JsonStorage(Path(tmp) / "cache.json", CacheEntry)),
                ("memory", MemoryStorage(CacheEntry)),
            ]
            for name, store in backends:
                with self.subTest(backend=name):
                    store.open()
                    store.upsert("IE00X", {"price": 7.5})
                    store.persist()
                    self.assertEqual(
                        store.snapshot(), {"IE00X": {"price": 7.5}}
                    )
                    store.close()

    def test_persist_is_noop_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            store = JsonStorage(path, CacheEntry)
            store.open()
            store.persist()
        self.assertFalse(path.exists())

    def test_snapshot_is_a_copy(self) -> None:
        store = MemoryStorage(CacheEntry, {"A": {"price": 1.0}})
        snap = store.snapshot()
        snap["A"]["price"] = 999.0
        entry = store.get("A")
        assert entry is not None
        self.assertEqual(entry.price, 1.0)


class TestCacheRepositoryLifecycle(unittest.TestCase):
    def test_restore_is_lenient_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, make in _backends(tmp):
                with self.subTest(backend=name):
                    repo = CacheRepository(make())
                    restored = repo.restore(
                        {"GOOD": {"price": 1.0}, "BAD": {"price": -5.0}}
                    )
                    self.assertEqual(restored, 1)
                    self.assertEqual(repo.parsed("GOOD"), (1.0, None, None))
                    self.assertEqual(repo.parsed("BAD"), (None, None, None))

    def test_open_snapshot_persist_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            repo = CacheRepository(JsonStorage(path, CacheEntry))
            repo.open()
            repo.stage("IE00X", price=4.25, update_price=True)
            repo.persist()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved, {"IE00X": {"price": 4.25}})
            fresh = CacheRepository(JsonStorage(path, CacheEntry))
            fresh.open()
            self.assertEqual(fresh.snapshot(), {"IE00X": {"price": 4.25}})
            fresh.close()
            repo.close()


# ---------------------------------------------------------------------------
# Repository composition: identical domain assertions on every backend
# ---------------------------------------------------------------------------
def _backends(
    tmp: str,
) -> list[tuple[str, Callable[[], Storage[Any]]]]:
    return [
        ("json", lambda: JsonStorage(Path(tmp) / "cache.json", CacheEntry)),
        ("memory", lambda: MemoryStorage(CacheEntry)),
    ]


class TestCacheRepositoryComposition(unittest.TestCase):
    def test_stage_partial_merge_and_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, make in _backends(tmp):
                with self.subTest(backend=name):
                    repo = CacheRepository(make())
                    self.assertIsNone(repo.stage("IE00X"))  # no flags → no-op
                    self.assertNotIn("IE00X", repo)
                    repo.stage("IE00X", price=10.0, update_price=True)
                    repo.stage(
                        "IE00X",
                        countries=[{"name": "France", "weight_pct": 50.0}],
                        update_countries=True,
                    )
                    self.assertEqual(
                        repo.parsed("IE00X"),
                        (10.0, {"France": 0.5}, None),
                    )

    def test_stage_quotes_and_clear_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name, make in _backends(tmp):
                with self.subTest(backend=name):
                    repo = CacheRepository(make())
                    count = repo.stage_quotes({"A": 1.5, "B": None, "": 9.0})
                    self.assertEqual(count, 1)
                    self.assertEqual(repo.parsed("A"), (1.5, None, None))
                    self.assertEqual(repo.parsed("missing"), (None, None, None))
                    repo.stage("A", sectors={"Tech": 0.5}, update_sectors=True)
                    self.assertTrue(repo.clear_fields("A", "sectors"))
                    self.assertFalse(repo.clear_fields("A", "sectors"))
                    self.assertFalse(repo.clear_fields("ghost", "sectors"))
                    entry = repo.get("A")
                    assert entry is not None
                    self.assertEqual(entry.price, 1.5)
                    self.assertIsNone(entry.sectors)


class TestAssetRepositoryComposition(unittest.TestCase):
    def _repo(self, tmp: str, kind: str) -> AssetRepository:
        if kind == "memory":
            position_backend = MemoryStorage(PositionRecord)
            portfolio_backend = MemoryStorage(UserPortfolioRecord)
        elif kind == "json":
            position_backend = JsonStorage(Path(tmp) / "positions.json", PositionRecord)
            portfolio_backend = JsonStorage(
                Path(tmp) / "portfolios.json", UserPortfolioRecord
            )
        else:
            db = Path(tmp) / "assets.db"
            position_backend = SqliteStorage(db, "user_positions", PositionRecord)
            portfolio_backend = SqliteStorage(
                db, "user_portfolios", UserPortfolioRecord
            )
        return AssetRepository(
            "default",
            position_repo=PositionRepository(position_backend),
            portfolio_repo=UserPortfolioRepository(portfolio_backend),
        )

    def test_set_get_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("json", "memory", "sqlite"):
                with self.subTest(backend=name):
                    repo = self._repo(tmp, name)
                    self.assertIsNone(repo.get_positions("equity_portfolio"))
                    repo.set_positions(
                        "equity_portfolio", [{"ISIN": "X", "value": 1.0}]
                    )
                    self.assertIn("equity_portfolio", repo)
                    rows = repo.get_positions("equity_portfolio")
                    assert rows is not None
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["ISIN"], "X")
                    self.assertEqual(rows[0]["value"], 1.0)
                    with self.assertRaises(TypeError):
                        repo.set_positions("bad", ["nope"])  # type: ignore[list-item]
                    repo.remove_positions("equity_portfolio")
                    self.assertIsNone(repo.get_positions("equity_portfolio"))

    def test_lifecycle_round_trip(self) -> None:
        data = {"equity_portfolio": [{"ISIN": "X", "value": 1.0}]}
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("json", "memory", "sqlite"):
                with self.subTest(backend=name):
                    repo = self._repo(tmp, name)
                    repo.open()
                    self.assertEqual(repo.restore(data), 1)
                    snapshot = repo.snapshot()
                    self.assertEqual(snapshot["equity_portfolio"][0]["ISIN"], "X")
                    self.assertEqual(
                        snapshot["equity_portfolio"][0]["value"], 1.0
                    )
                    repo.persist()
                    repo.close()

    def test_restore_skips_bad_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, "memory")
            restored = repo.restore(
                {
                    "good": [{"ISIN": "X"}],
                    "bad": "not-a-list",
                    "also-bad": [{"ISIN": "Y"}, "nope"],
                }
            )
            self.assertEqual(restored, 1)
            good = repo.get_positions("good")
            assert good is not None
            self.assertEqual(good[0]["ISIN"], "X")
            self.assertIsNone(repo.get_positions("bad"))


def _empty_registry() -> IsinRegistry:
    return IsinRegistry(MemoryStorage(IsinRecord))


class TestIsinRegistryComposition(unittest.TestCase):
    def test_explicit_rows_round_trip(self) -> None:
        registry = _empty_registry()
        registry.backend.put(
            IsinRecord(
                "IE00BKM4GZ66",
                {
                    "issuer": "ishares",
                    "bucket": "equity_portfolio",
                    "product_ref": "264659",
                },
            )
        )
        self.assertEqual(registry.get_issuer_for_isin("IE00BKM4GZ66"), "ishares")
        self.assertEqual(registry.get_product_ref_for_isin("IE00BKM4GZ66"), "264659")
        self.assertEqual(
            registry.get_bucket_for_isin("IE00BKM4GZ66"), "equity_portfolio"
        )
        self.assertIsNone(registry.get_issuer_for_isin("unknown"))
        self.assertIsNone(registry.get_bucket_for_isin("unknown"))
        self.assertIsNone(registry.get_product_ref_for_isin("unknown"))
        self.assertIn("IE00BKM4GZ66", registry)

    def test_registry_over_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            registry = IsinRegistry(JsonStorage(path, IsinRecord))
            registry.open()
            registry.backend.put(
                IsinRecord(
                    "IE00BKM4GZ66",
                    {
                        "issuer": "ishares",
                        "bucket": "equity_portfolio",
                        "product_ref": "264659",
                    },
                )
            )
            self.assertEqual(registry.get_issuer_for_isin("IE00BKM4GZ66"), "ishares")
            self.assertEqual(registry.get_product_ref_for_isin("IE00BKM4GZ66"), "264659")
            registry.persist()
            fresh = IsinRegistry(JsonStorage(path, IsinRecord))
            fresh.open()
            self.assertEqual(fresh.get_issuer_for_isin("IE00BKM4GZ66"), "ishares")
            self.assertEqual(
                fresh.get_product_ref_for_isin("IE00BKM4GZ66"), "264659"
            )

    def test_register_isin_fills_only_missing(self) -> None:
        registry = _empty_registry()
        record = registry.register_isin("IE00NEWX01", issuer="dws", bucket="equity_portfolio")
        self.assertEqual(record.issuer, "dws")
        # Existing decided values are never overwritten...
        same = registry.register_isin("IE00NEWX01", issuer="amundi", bucket="bond_portfolio")
        self.assertEqual(same.issuer, "dws")
        self.assertEqual(same.bucket, "equity_portfolio")
        # ...but null fields get filled.
        registry.register_isin("IE00NEWX02", bucket="equity_portfolio")
        filled = registry.register_isin("IE00NEWX02", issuer="ubs")
        self.assertEqual(filled.issuer, "ubs")
        self.assertEqual(filled.bucket, "equity_portfolio")
        with self.assertRaises(ValueError):
            registry.register_isin("IE00BADX01", issuer="nope")

    def test_set_issuer_overwrites(self) -> None:
        registry = _empty_registry()
        registry.set_issuer_for_isin("IE00NEWX03", "dws")
        self.assertEqual(registry.get_issuer_for_isin("IE00NEWX03"), "dws")
        registry.set_issuer_for_isin("IE00NEWX03", "ubs")
        self.assertEqual(registry.get_issuer_for_isin("IE00NEWX03"), "ubs")
        with self.assertRaises(ValueError):
            registry.set_issuer_for_isin("IE00NEWX03", "nope")


if __name__ == "__main__":
    unittest.main()
