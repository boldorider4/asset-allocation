# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``curate_isin_names`` maintenance script."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.curate_isin_names import curate_isin_names
from tests.store_helpers import db_path, read_isin, write_positions, write_isin


_NAME = "iShares Core MSCI World UCITS ETF"


class TestCurateIsinNames(unittest.TestCase):
    def test_backfills_missing_names_and_uses_registry_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4L5Y983": {
                        "issuer": "ishares",
                        "bucket": "equity_portfolio",
                        "name": _NAME,
                    },
                    "IE00BKM4GZ66": {
                        "issuer": "ishares",
                        "bucket": "equity_portfolio",
                        "product_ref": "264659",
                    },
                    "IE00BMTX1Y45": None,
                },
            )
            fetched: set[str] = set()

            def fetch_name(isin: str) -> str | None:
                fetched.add(isin)
                return f"{isin} Fund"

            counts = curate_isin_names(
                db, fetch_name=fetch_name, emit=lambda _message: None
            )

            self.assertEqual(counts, (2, 1, 0, 0))
            self.assertEqual(fetched, {"IE00BKM4GZ66", "IE00BMTX1Y45"})
            rows = read_isin(db)
            self.assertEqual(
                rows["IE00B4L5Y983"]["name"], _NAME
            )
            self.assertEqual(
                rows["IE00BKM4GZ66"]["name"], "IE00BKM4GZ66 Fund"
            )
            self.assertEqual(
                rows["IE00BKM4GZ66"]["product_ref"], "264659"
            )
            self.assertEqual(
                rows["IE00BMTX1Y45"]["name"], "IE00BMTX1Y45 Fund"
            )

    def test_missing_name_keeps_registry_row_and_counts_unresolved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00B4L5Y983": {"issuer": "ishares"}})

            counts = curate_isin_names(
                db,
                fetch_name=lambda _isin: None,
                emit=lambda _message: None,
            )

            self.assertEqual(counts, (0, 0, 1, 0))
            row = read_isin(db)["IE00B4L5Y983"]
            self.assertIsNone(row["name"])
            self.assertEqual(row["issuer"], "ishares")

    def test_users_flag_limits_the_run_to_position_isins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4L5Y983": None,
                    "IE00BKM4GZ66": None,
                    "IE00OTHER": None,
                },
            )
            write_positions(
                db,
                {
                    "equity_portfolio": [
                        {"name": "A", "isin": "IE00B4L5Y983", "shares": 1}
                    ]
                },
                user="alice",
            )
            write_positions(
                db,
                {
                    "bond_portfolio": [
                        {"name": "B", "isin": "IE00BKM4GZ66", "shares": 1}
                    ]
                },
                user="bob",
            )
            fetched: set[str] = set()

            def fetch_name(isin: str) -> str:
                fetched.add(isin)
                return "Res"

            counts = curate_isin_names(
                db,
                fetch_name=fetch_name,
                users=True,
                emit=lambda _message: None,
            )

            self.assertEqual(fetched, {"IE00B4L5Y983", "IE00BKM4GZ66"})
            self.assertEqual(counts, (2, 0, 0, 0))
            rows = read_isin(db)
            self.assertEqual(rows["IE00B4L5Y983"]["name"], "Res")
            self.assertEqual(rows["IE00BKM4GZ66"]["name"], "Res")
            self.assertIsNone(rows["IE00OTHER"]["name"])

    def test_dry_run_does_not_persist_or_create_a_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00B4L5Y983": {"issuer": "ishares"}})

            counts = curate_isin_names(
                db,
                fetch_name=lambda _isin: _NAME,
                dry_run=True,
                emit=lambda _message: None,
            )

            self.assertEqual(counts, (1, 0, 0, 0))
            row = read_isin(db)["IE00B4L5Y983"]
            self.assertIsNone(row["name"])
            self.assertEqual(list(db.parent.glob("*.json")), [])

    def test_resume_with_a_run_file_skips_examined_and_retries_failed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4L5Y983": {
                        "issuer": "ishares",
                        "name": _NAME,
                    },
                    "IE00BKM4GZ66": {"issuer": "ishares"},
                    "IE00BMTX1Y45": {"issuer": "ishares"},
                },
            )
            run_file = db.parent / "resume.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(db.resolve()),
                        "users": False,
                        "examined": ["IE00B4L5Y983"],
                        "failed": ["IE00BKM4GZ66"],
                        "last_isin": "IE00BKM4GZ66",
                    }
                ),
                encoding="utf-8",
            )
            fetched: set[str] = set()

            counts = curate_isin_names(
                db,
                fetch_name=lambda isin: (
                    fetched.add(isin), f"{isin} Fund"
                )[1],
                run_file=run_file,
                emit=lambda _message: None,
            )

            self.assertEqual(
                fetched, {"IE00BKM4GZ66", "IE00BMTX1Y45"}
            )
            self.assertEqual(counts, (2, 0, 0, 0))
            row = read_isin(db)["IE00BKM4GZ66"]
            self.assertEqual(row["name"], "IE00BKM4GZ66 Fund")
            self.assertEqual(read_isin(db)["IE00BMTX1Y45"]["name"], "IE00BMTX1Y45 Fund")
            self.assertFalse(run_file.exists())

    def test_run_file_is_kept_when_a_name_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00B4L5Y983": {"issuer": "ishares"}})
            run_file = db.parent / "unresolved.json"
            run_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "db_file": str(db.resolve()),
                        "users": False,
                        "examined": [],
                        "failed": [],
                        "last_isin": None,
                    }
                ),
                encoding="utf-8",
            )

            counts = curate_isin_names(
                db,
                fetch_name=lambda _isin: None,
                run_file=run_file,
                emit=lambda _message: None,
            )

            self.assertEqual(counts, (0, 0, 1, 0))
            self.assertTrue(run_file.exists())
            payload = json.loads(run_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["failed"], ["IE00B4L5Y983"])

    def test_missing_run_file_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00B4L5Y983": None})
            with self.assertRaisesRegex(ValueError, "run file not found"):
                curate_isin_names(
                    db,
                    run_file=db.parent / "nope.json",
                    emit=lambda _message: None,
                )


if __name__ == "__main__":
    unittest.main()
