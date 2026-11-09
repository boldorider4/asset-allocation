# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``rescan_isin_asset_classes`` maintenance script."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.rescan_isin_asset_classes import rescan_asset_classes
from tests.store_helpers import db_path, read_isin, write_isin, write_positions


class TestRescanIsinAssetClasses(unittest.TestCase):
    def test_backfills_missing_buckets_and_keeps_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4L5Y983": {
                        "issuer": "ishares",
                        "bucket": "equity_portfolio",
                        "product_ref": "251736",
                        "visits": 3,
                    },
                    "IE00BKM4GZ66": {"issuer": "ishares"},
                },
            )

            counts = rescan_asset_classes(
                db,
                fetch_asset_class=lambda _isin: "equity_portfolio",
                emit=lambda _message: None,
            )

            self.assertEqual(counts, (1, 1, 0, 0))
            rows = read_isin(db)
            self.assertEqual(
                rows["IE00B4L5Y983"]["bucket"], "equity_portfolio"
            )
            self.assertEqual(
                rows["IE00B4L5Y983"]["product_ref"], "251736"
            )
            self.assertEqual(rows["IE00B4L5Y983"]["visits"], 3)
            self.assertEqual(
                rows["IE00BKM4GZ66"]["bucket"], "equity_portfolio"
            )
            self.assertEqual(rows["IE00BKM4GZ66"]["issuer"], "ishares")

    def test_users_flag_limits_the_run_to_position_isins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(
                db,
                {
                    "IE00B4L5Y983": None,
                    "IE00BKM4GZ66": None,
                    "IE00OTHER1": None,
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
            fetched: set[str] = set()

            def fetch_asset_class(isin: str) -> str:
                fetched.add(isin)
                return "bond_portfolio"

            counts = rescan_asset_classes(
                db,
                fetch_asset_class=fetch_asset_class,
                users=True,
                emit=lambda _message: None,
            )

            self.assertEqual(fetched, {"IE00B4L5Y983"})
            self.assertEqual(counts, (1, 0, 0, 0))
            rows = read_isin(db)
            self.assertEqual(
                rows["IE00B4L5Y983"]["bucket"], "bond_portfolio"
            )
            self.assertIsNone(rows["IE00OTHER1"]["bucket"])

    def test_dry_run_does_not_persist_or_create_a_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = db_path(tmp)
            write_isin(db, {"IE00B4L5Y983": {"issuer": "ishares"}})

            counts = rescan_asset_classes(
                db,
                fetch_asset_class=lambda _isin: "bond_portfolio",
                dry_run=True,
                emit=lambda _message: None,
            )

            self.assertEqual(counts, (1, 0, 0, 0))
            self.assertIsNone(read_isin(db)["IE00B4L5Y983"]["bucket"])
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
                        "bucket": "equity_portfolio",
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

            counts = rescan_asset_classes(
                db,
                fetch_asset_class=lambda isin: (
                    fetched.add(isin), "bond_portfolio"
                )[1],
                run_file=run_file,
                emit=lambda _message: None,
            )

            self.assertEqual(
                fetched, {"IE00BKM4GZ66", "IE00BMTX1Y45"}
            )
            self.assertEqual(counts, (2, 0, 0, 0))
            rows = read_isin(db)
            self.assertEqual(
                rows["IE00B4L5Y983"]["bucket"], "equity_portfolio"
            )
            self.assertEqual(
                rows["IE00BKM4GZ66"]["bucket"], "bond_portfolio"
            )
            self.assertEqual(
                rows["IE00BMTX1Y45"]["bucket"], "bond_portfolio"
            )
            self.assertFalse(run_file.exists())

    def test_run_file_is_kept_when_a_bucket_is_unresolved(self) -> None:
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

            counts = rescan_asset_classes(
                db,
                fetch_asset_class=lambda _isin: None,
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
                rescan_asset_classes(
                    db,
                    run_file=db.parent / "nope.json",
                    emit=lambda _message: None,
                )


if __name__ == "__main__":
    unittest.main()
