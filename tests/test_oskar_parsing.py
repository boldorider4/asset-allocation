"""Unit tests for OSKAR DOM row merge and category resolution (no Playwright)."""

from __future__ import annotations

import unittest

from oskar import (
    _OSKAR_CATEGORY_ANLEIHEN,
    _OSKAR_CATEGORY_AKTIEN,
    _merge_row_snapshots_into,
    _oskar_category_from_row,
)


class TestOskarCategoryFromRow(unittest.TestCase):
    def test_prefers_level1_category(self) -> None:
        self.assertEqual(
            _oskar_category_from_row(category="Anleihen", subcategory="Anleihen Global"),
            "Anleihen",
        )

    def test_infers_from_subcategory(self) -> None:
        self.assertEqual(
            _oskar_category_from_row(category="", subcategory="Aktien USA"),
            _OSKAR_CATEGORY_AKTIEN,
        )
        self.assertEqual(
            _oskar_category_from_row(category="", subcategory="Anleihen Global"),
            _OSKAR_CATEGORY_ANLEIHEN,
        )

    def test_empty_when_unknown(self) -> None:
        self.assertEqual(_oskar_category_from_row(category="", subcategory=""), "")
        self.assertEqual(_oskar_category_from_row(category="", subcategory="Unknown"), "")


class TestMergeRowSnapshots(unittest.TestCase):
    def test_backfills_subcategory_when_keeping_shorter_raw(self) -> None:
        ordered: list[dict] = [
            {
                "isin": "LU0123456789",
                "raw": "short",
                "category": "",
                "subcategory": "",
                "frameUrl": "",
            }
        ]
        idx = {"LU0123456789": 0}
        _merge_row_snapshots_into(
            ordered,
            idx,
            [
                {
                    "isin": "LU0123456789",
                    "raw": "x",
                    "category": "Anleihen",
                    "subcategory": "Anleihen Global",
                    "frameUrl": "",
                }
            ],
        )
        row = ordered[0]
        self.assertEqual(row["raw"], "short")
        self.assertEqual(row["category"], "Anleihen")
        self.assertEqual(row["subcategory"], "Anleihen Global")


if __name__ == "__main__":
    unittest.main()
