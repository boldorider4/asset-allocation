# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the standalone ``storage`` module (no wiring to the app yet).

Run from repo root::

    pytest tests/test_json_storage.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage import (  # noqa: E402
    AssetStore,
    CacheEntry,
    CacheStore,
    DEFAULT_ISIN_RECORDS,
    IsinRecord,
    IsinRegistryStore,
)


class TestCacheEntry(unittest.TestCase):
    def test_partial_merge_preserves_other_fields(self) -> None:
        entry = CacheEntry("IE00X", {"price": 10.0, "countries": {"France": 0.5}})
        entry.merge({"sectors": {"Technology": 0.3}})
        self.assertEqual(entry.price, 10.0)
        self.assertEqual(entry.countries, {"France": 0.5})
        self.assertEqual(entry.sectors, {"Technology": 0.3})

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

    def test_row_converters(self) -> None:
        fractions = CacheEntry.rows_to_fractions(
            [{"name": "France", "weight_pct": 50.0}]
        )
        self.assertEqual(fractions, {"France": 0.5})
        rows = CacheEntry.fractions_to_rows({"France": 0.5})
        self.assertEqual(rows, [{"name": "France", "weight_pct": 50.0}])
        self.assertIsNone(CacheEntry.fractions_to_rows(None))

    def test_parsed_tuple(self) -> None:
        entry = CacheEntry(
            "IE00X", {"price": 2.0, "countries": {"A": 1.0}, "sectors": {"B": 1.0}}
        )
        self.assertEqual(
            entry.parsed(), (2.0, {"A": 1.0}, {"B": 1.0})
        )


class TestCacheStore(unittest.TestCase):
    def test_upsert_round_trips_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            store = CacheStore(path)
            store.upsert("IE00X", {"price": 12.5})
            store.upsert("IE00X", {"countries": {"France": 0.5}})
            self.assertTrue(store.is_dirty)
            store.save()
            self.assertFalse(store.is_dirty)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["IE00X"]["price"], 12.5)
        self.assertEqual(saved["IE00X"]["countries"], {"France": 0.5})

    def test_save_is_noop_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            store = CacheStore(path)
            store.load()
            store.save()
        self.assertFalse(path.exists())

    def test_missing_and_corrupt_files_start_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = CacheStore(Path(tmp) / "nope.json")
            self.assertEqual(len(missing), 0)
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(len(CacheStore(bad)), 0)
            wrong_root = Path(tmp) / "list.json"
            wrong_root.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(len(CacheStore(wrong_root)), 0)

    def test_bad_row_skipped_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text(
                json.dumps(
                    {"GOOD": {"price": 1.0}, "BAD": {"price": -5.0}}
                ),
                encoding="utf-8",
            )
            store = CacheStore(path)
            self.assertIn("GOOD", store)
            self.assertNotIn("BAD", store)

    def test_add_rejects_duplicates_and_clear_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CacheStore(Path(tmp) / "c.json")
            store.add(CacheEntry("IE00X", {"price": 1.0}))
            with self.assertRaises(KeyError):
                store.add(CacheEntry("IE00X", {"price": 2.0}))
            store.upsert("IE00X", {"sectors": {"Tech": 0.5}})
            self.assertTrue(store.clear_fields("IE00X", "sectors"))
            self.assertIsNone(store.get("IE00X").sectors)  # type: ignore[union-attr]
            self.assertEqual(store.get("IE00X").price, 1.0)  # type: ignore[union-attr]


class TestAssetStore(unittest.TestCase):
    def test_buckets_live_in_their_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            assets_path = Path(tmp) / "assets.json"
            assets = AssetStore(assets_path)
            assets.set_positions(
                "equity_portfolio", [{"ISIN": "X", "value": 1.0}]
            )
            assets.save()
            self.assertFalse(cache_path.exists())
            saved = json.loads(assets_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["equity_portfolio"], [{"ISIN": "X", "value": 1.0}])

    def test_bucket_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = AssetStore(Path(tmp) / "a.json")
            with self.assertRaises(TypeError):
                assets.set_positions("b", ["not-a-dict"])  # type: ignore[list-item]


class TestIsinRegistry(unittest.TestCase):
    def test_seed_covers_all_issuers(self) -> None:
        store = IsinRegistryStore.seeded()
        self.assertTrue(store.belongs_to("IE000BI8OT95", "amundi"))
        self.assertTrue(store.belongs_to("IE00BKM4GZ66", "ishares"))
        self.assertTrue(store.belongs_to("IE00B4YBJ215", "ssga"))
        self.assertTrue(store.belongs_to("IE0006WW1TQ4", "dws"))
        self.assertTrue(store.belongs_to("IE00BD4TXV59", "ubs"))
        self.assertTrue(store.belongs_to("IE00BKS7L097", "invesco"))
        self.assertTrue(store.belongs_to("IE00BFXR5W90", "landg"))
        self.assertFalse(store.belongs_to("IE00BKM4GZ66", "amundi"))
        self.assertEqual(store.product_ref_for("IE00BKM4GZ66"), "264659")
        self.assertIsNone(store.product_ref_for("IE000BI8OT95"))
        self.assertEqual(len(store), len(DEFAULT_ISIN_RECORDS))

    def test_bad_issuer_rejected(self) -> None:
        with self.assertRaises(ValueError):
            IsinRecord("IE00X", {"issuer": "nope"})


if __name__ == "__main__":
    unittest.main()
