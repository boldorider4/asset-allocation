# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ISIN registry listing and rescan scripts."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from position.factory import _IssuerProbe  # noqa: E402
from scripts.list_justetf_isins import list_justetf_isins  # noqa: E402
from scripts.rescan_isin_issuers import (  # noqa: E402
    _normalize_isins,
    rescan_isins,
)
from tests.store_helpers import db_path, read_isin, write_isin  # noqa: E402


class TestListJustetfIsins(unittest.TestCase):
    def test_lists_only_justetf_rows_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B": {"issuer": "justetf", "bucket": "equity_portfolio"},
                    "IE00A": {"issuer": "justetf", "bucket": "equity_portfolio"},
                    "IE00C": {"issuer": "ishares", "bucket": "equity_portfolio"},
                    "IE00D": {"issuer": None, "bucket": "equity_portfolio"},
                },
            )
            self.assertEqual(list_justetf_isins(db), ["IE00A", "IE00B"])

    def test_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                list_justetf_isins(db_path(tmp))

    def test_missing_registry_table_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE other (key TEXT PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(ValueError):
                list_justetf_isins(db)


class TestRescanIsins(unittest.TestCase):
    def test_upgrades_justetf_and_registers_product_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00BKM4GZ66": {
                        "issuer": "justetf",
                        "bucket": "equity_portfolio",
                    }
                },
            )
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00BKM4GZ66"],
                probe=lambda *_: _IssuerProbe("ishares", "264659"),
                emit=messages.append,
            )
            self.assertEqual(counts, (1, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["product_ref"], "264659")
            self.assertEqual(row["bucket"], "equity_portfolio")
            self.assertIn("justetf -> ishares", messages[0])

    def test_probe_miss_leaves_justetf_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00MISSING": {"issuer": "justetf"}})
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00MISSING"],
                probe=lambda *_: None,
                emit=messages.append,
            )
            self.assertEqual(counts, (0, 1, 0))
            self.assertEqual(read_isin(db)["IE00MISSING"]["issuer"], "justetf")
            self.assertIn("unchanged (justetf)", messages[0])

    def test_specialized_conflict_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4YBJ215": {
                        "issuer": "ssga",
                        "bucket": "equity_portfolio",
                        "product_ref": "spy4-gy",
                    }
                },
            )
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00B4YBJ215"],
                probe=lambda *_: _IssuerProbe("dws"),
                emit=messages.append,
            )
            self.assertEqual(counts, (0, 1, 0))
            row = read_isin(db)["IE00B4YBJ215"]
            self.assertEqual(row["issuer"], "ssga")
            self.assertEqual(row["product_ref"], "spy4-gy")
            self.assertIn("kept ssga", messages[0])

    def test_matching_specialized_issuer_fills_missing_product_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00BKM4GZ66": {"issuer": "ishares"}})
            counts = rescan_isins(
                db,
                ["IE00BKM4GZ66"],
                probe=lambda *_: _IssuerProbe("ishares", "264659"),
                emit=lambda _message: None,
            )
            self.assertEqual(counts, (1, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["product_ref"], "264659")

    def test_existing_product_ref_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00BKM4GZ66": {
                        "issuer": "ishares",
                        "product_ref": "old-id",
                    }
                },
            )
            counts = rescan_isins(
                db,
                ["IE00BKM4GZ66"],
                probe=lambda *_: _IssuerProbe("ishares", "264659"),
                emit=lambda _message: None,
            )
            self.assertEqual(counts, (0, 1, 0))
            self.assertEqual(read_isin(db)["IE00BKM4GZ66"]["product_ref"], "old-id")

    def test_one_error_does_not_stop_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00BOOM": {"issuer": "justetf"},
                    "IE00GOOD": {"issuer": "justetf"},
                },
            )

            def probe(isin: str, _name: str | None, _registry: object):
                if isin == "IE00BOOM":
                    raise RuntimeError("boom")
                return _IssuerProbe("ishares", "264659")

            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00BOOM", "IE00GOOD"],
                probe=probe,
                emit=messages.append,
            )
            self.assertEqual(counts, (1, 0, 1))
            rows = read_isin(db)
            self.assertEqual(rows["IE00BOOM"]["issuer"], "justetf")
            self.assertEqual(rows["IE00GOOD"]["issuer"], "ishares")
            self.assertIn("error: boom", messages[0])

    def test_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                rescan_isins(db_path(tmp), ["IE00BKM4GZ66"])

    def test_normalize_isins_strips_uppercases_and_deduplicates(self) -> None:
        self.assertEqual(
            _normalize_isins([" ie00bkm4gz66 ", "IE00BKM4GZ66", "", "ie00abc"]),
            ["IE00BKM4GZ66", "IE00ABC"],
        )


if __name__ == "__main__":
    unittest.main()
