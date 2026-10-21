# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chart files on disk follow the portfolio that produced them.

The ``*.raw`` files are derived data. A user whose portfolio became
empty must not keep the charts of the last run, and a missing chart
directory is not an error.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from utils import chart_files, clear_chart_files
from visual.web.backend.constituents import assets_positions_left


class TestClearChartFiles(unittest.TestCase):
    def test_removes_only_the_chart_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "01-a.raw").write_text("{}", encoding="utf-8")
            (directory / "02-b.raw").write_text("{}", encoding="utf-8")
            keep = directory / "index.html"
            keep.write_text("DASHBOARD", encoding="utf-8")

            self.assertEqual(clear_chart_files(directory), 2)
            self.assertEqual(list(directory.glob("*.raw")), [])
            self.assertTrue(keep.exists())

    def test_missing_directory_is_no_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clear_chart_files(Path(tmp) / "nope"), 0)

    def test_empty_directory_removes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clear_chart_files(Path(tmp)), 0)

    def test_chart_files_lists_the_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "01-a.raw").write_text("{}", encoding="utf-8")
            (directory / "notes.txt").write_text("x", encoding="utf-8")
            self.assertEqual(
                [p.name for p in chart_files(directory)], ["01-a.raw"]
            )
            self.assertEqual(chart_files(Path(tmp) / "nope"), [])


class TestAssetsPositionsLeft(unittest.TestCase):
    def test_counts_the_positions_of_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text(
                json.dumps(
                    {
                        "alice": {
                            "equity_portfolio": [
                                {"name": "A", "ISIN": "IE00A"},
                                {"name": "B", "ISIN": "IE00B"},
                            ],
                            "bond_portfolio": [
                                {"name": "C", "ISIN": "IE00C"},
                            ],
                        },
                        "bob": {"equity_portfolio": []},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(assets_positions_left(assets, "alice"), 3)
            self.assertEqual(assets_positions_left(assets, "bob"), 0)

    def test_unknown_user_has_no_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets.json"
            assets.write_text(json.dumps({"alice": {}}), encoding="utf-8")
            self.assertEqual(assets_positions_left(assets, "nobody"), 0)


if __name__ == "__main__":
    unittest.main()
