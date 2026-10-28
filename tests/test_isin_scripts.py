# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ISIN registry listing and rescan scripts."""

from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from position.factory import _IssuerProbe  # noqa: E402
from scripts.list_justetf_isins import list_justetf_isins  # noqa: E402
from scripts.rescan_isin_issuers import (  # noqa: E402
    _normalize_isins,
    main as rescan_main,
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
            self.assertEqual(counts, (1, 0, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["product_ref"], "264659")
            self.assertEqual(row["bucket"], "equity_portfolio")
            self.assertEqual(row["visits"], 1)
            self.assertIn("justetf -> ishares", messages[0])

    def test_probe_miss_leaves_justetf_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00MISSING": {"issuer": "justetf"}})
            justetf_exists = Mock(return_value=True)
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00MISSING"],
                probe=lambda *_: None,
                justetf_exists=justetf_exists,
                emit=messages.append,
            )
            self.assertEqual(counts, (0, 0, 1, 0))
            row = read_isin(db)["IE00MISSING"]
            self.assertEqual(row["issuer"], "justetf")
            self.assertEqual(row["visits"], 1)
            self.assertIn("unchanged (justetf)", messages[0])
            justetf_exists.assert_not_called()

    def test_missing_row_is_verified_as_justetf_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {})
            calls: list[str] = []

            def probe(_isin: str, _name: str | None, _registry: object):
                calls.append("probe")
                return None

            def justetf_exists(_isin: str) -> bool:
                calls.append("justetf")
                return True

            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["BG9000011163"],
                probe=probe,
                justetf_exists=justetf_exists,
                emit=messages.append,
            )
            self.assertEqual(counts, (1, 0, 0, 0))
            self.assertEqual(calls, ["probe", "justetf"])
            row = read_isin(db)["BG9000011163"]
            self.assertEqual(row["issuer"], "justetf")
            self.assertEqual(row["bucket"], "equity_portfolio")
            self.assertIsNone(row["product_ref"])
            self.assertEqual(row["visits"], 1)
            self.assertIn("missing -> justetf", messages[0])

    def test_missing_row_not_on_justetf_stays_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {})
            justetf_exists = Mock(return_value=False)
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["IE00NOTFOUND"],
                probe=lambda *_: None,
                justetf_exists=justetf_exists,
                emit=messages.append,
            )
            self.assertEqual(counts, (0, 0, 1, 0))
            row = read_isin(db)["IE00NOTFOUND"]
            self.assertIsNone(row["issuer"])
            self.assertIsNone(row["bucket"])
            self.assertEqual(row["visits"], 1)
            self.assertIn("not found on JustETF", messages[0])
            justetf_exists.assert_called_once_with("IE00NOTFOUND")

    def test_specialized_upgrade_fills_default_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {})
            justetf_exists = Mock()
            counts = rescan_isins(
                db,
                ["IE00BKM4GZ66"],
                probe=lambda *_: _IssuerProbe("ishares", "264659"),
                justetf_exists=justetf_exists,
                emit=lambda _message: None,
            )
            self.assertEqual(counts, (1, 0, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["bucket"], "equity_portfolio")
            self.assertEqual(row["product_ref"], "264659")
            self.assertEqual(row["visits"], 1)
            justetf_exists.assert_not_called()

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
            self.assertEqual(counts, (0, 0, 1, 0))
            row = read_isin(db)["IE00B4YBJ215"]
            self.assertEqual(row["issuer"], "ssga")
            self.assertEqual(row["product_ref"], "spy4-gy")
            self.assertEqual(row["visits"], 1)
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
            self.assertEqual(counts, (1, 0, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["product_ref"], "264659")
            self.assertEqual(row["visits"], 1)

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
            self.assertEqual(counts, (0, 0, 1, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["product_ref"], "old-id")
            self.assertEqual(row["visits"], 1)

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
            self.assertEqual(counts, (1, 0, 0, 1))
            rows = read_isin(db)
            self.assertEqual(rows["IE00BOOM"]["issuer"], "justetf")
            self.assertEqual(rows["IE00GOOD"]["issuer"], "ishares")
            self.assertEqual(rows["IE00BOOM"]["visits"], 1)
            self.assertEqual(rows["IE00GOOD"]["visits"], 1)
            self.assertIn("error: boom", messages[0])

    def test_second_scan_skips_a_visited_isin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00SKIP": {"issuer": "justetf"}})
            first = rescan_isins(
                db,
                ["IE00SKIP"],
                probe=lambda *_: None,
                emit=lambda _message: None,
            )
            self.assertEqual(first, (0, 0, 1, 0))

            probe = Mock(return_value=None)
            messages: list[str] = []
            second = rescan_isins(
                db,
                ["IE00SKIP"],
                probe=probe,
                emit=messages.append,
            )
            self.assertEqual(second, (0, 1, 0, 0))
            probe.assert_not_called()
            self.assertIn("skipped (visited 1x", messages[0])
            self.assertEqual(read_isin(db)["IE00SKIP"]["visits"], 1)

    def test_force_rescans_a_visited_isin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00FORCE": {"issuer": "justetf"}})
            first = rescan_isins(
                db,
                ["IE00FORCE"],
                probe=lambda *_: None,
                emit=lambda _message: None,
            )
            self.assertEqual(first, (0, 0, 1, 0))

            probe = Mock(return_value=_IssuerProbe("ishares", "264659"))
            counts = rescan_isins(
                db,
                ["IE00FORCE"],
                force=True,
                probe=probe,
                emit=lambda _message: None,
            )
            self.assertEqual(counts, (1, 0, 0, 0))
            probe.assert_called_once()
            row = read_isin(db)["IE00FORCE"]
            self.assertEqual(row["issuer"], "ishares")
            self.assertEqual(row["visits"], 2)

    def test_blacklisted_isin_is_always_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"LU2903252349": {"issuer": "justetf"}})
            probe = Mock(return_value=_IssuerProbe("ishares", "264659"))
            messages: list[str] = []
            counts = rescan_isins(
                db,
                ["LU2903252349"],
                force=True,
                probe=probe,
                emit=messages.append,
            )
            self.assertEqual(counts, (0, 1, 0, 0))
            probe.assert_not_called()
            self.assertIn("skipped (blacklisted)", messages[0])
            self.assertEqual(read_isin(db)["LU2903252349"]["visits"], 0)

    def test_failed_probe_counts_as_a_visit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00FAIL": {"issuer": "justetf"}})
            counts = rescan_isins(
                db,
                ["IE00FAIL"],
                probe=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
                emit=lambda _message: None,
            )
            self.assertEqual(counts, (0, 0, 0, 1))
            self.assertEqual(read_isin(db)["IE00FAIL"]["visits"], 1)

            probe = Mock(return_value=None)
            second = rescan_isins(
                db,
                ["IE00FAIL"],
                probe=probe,
                emit=lambda _message: None,
            )
            self.assertEqual(second, (0, 1, 0, 0))
            probe.assert_not_called()

    def test_missing_database_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                rescan_isins(db_path(tmp), ["IE00BKM4GZ66"])

    def test_missing_visits_column_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "CREATE TABLE isin_registry "
                    "(isin TEXT PRIMARY KEY, issuer TEXT, bucket TEXT, "
                    "product_ref TEXT)"
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(
                ValueError, "v3.0.0_add_isin_registry_visits"
            ):
                rescan_isins(db, ["IE00BKM4GZ66"])

    def test_normalize_isins_splits_strips_uppercases_and_deduplicates(
        self,
    ) -> None:
        self.assertEqual(
            _normalize_isins(
                " ie00bkm4gz66 , IE00BKM4GZ66,, ie00abc , "
            ),
            ["IE00BKM4GZ66", "IE00ABC"],
        )

    def test_main_passes_comma_list_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            with (
                patch(
                    "scripts.rescan_isin_issuers.rescan_isins",
                    return_value=(0, 0, 0, 0),
                ) as rescan,
                redirect_stderr(io.StringIO()),
            ):
                code = rescan_main(
                    ["--db-file", str(db), "--force", "ie00a, IE00B,ie00a"]
                )
            self.assertEqual(code, 0)
            rescan.assert_called_once_with(
                db, ["IE00A", "IE00B"], force=True
            )


if __name__ == "__main__":
    unittest.main()
