"""Unit tests for OSKAR DOM row merge and category resolution (no Playwright)."""

from __future__ import annotations

import unittest

from oskar import (
    _OSKAR_CATEGORY_ANLEIHEN,
    _OSKAR_CATEGORY_AKTIEN,
    _merge_row_snapshots_into,
    _oskar_category_from_row,
    _parse_row_blob,
    _parse_tagesgeld_blob,
)


class TestParseRowBlob(unittest.TestCase):
    def test_single_line_blob_from_oskar_dom(self) -> None:
        isin = "IE00BF4RFH31"
        blob = (
            "iShares MSCI World Small Cap UCITS ETF IE00BF4RFH31 "
            "31,12 % 1.973,22 €"
        )
        name, weight, value = _parse_row_blob(blob, isin)
        self.assertEqual(name, "iShares MSCI World Small Cap UCITS ETF")
        self.assertEqual(weight, 31.12)
        self.assertEqual(value, 1973.22)

    def test_multiline_blob_still_works(self) -> None:
        isin = "IE00B4L5Y983"
        blob = "iShares Core MSCI World UCITS ETF\nIE00B4L5Y983\n12,5 %\n1.234,56 €"
        name, weight, value = _parse_row_blob(blob, isin)
        self.assertEqual(name, "iShares Core MSCI World UCITS ETF")
        self.assertEqual(weight, 12.5)
        self.assertEqual(value, 1234.56)


class TestParseTagesgeldBlob(unittest.TestCase):
    def test_parses_percent_and_euro(self) -> None:
        weight, value = _parse_tagesgeld_blob("Tagesgeld 2,5 % 999,33 €")
        self.assertEqual(weight, 2.5)
        self.assertEqual(value, 999.33)


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
