# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for reading/writing one user's portfolio through the repository.

Run from repo root::

    pytest tests/test_utils.py -v

The write test seeds and reads the SQLite database through the same
repository the app uses, so the round-trip assertion exercises the real
store rather than a test-only file format.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Make ``utils`` importable regardless of how the test is invoked
# (pytest from a subdir, ``python tests/test_utils.py``, IDE runners, ...).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.assets_helpers import read_user_buckets  # noqa: E402
from tests.store_helpers import (  # noqa: E402
    db_path,
    make_context,
    read_positions,
)

SAMPLE_ASSETS = REPO_ROOT / "assets.sample.json"


class TestLoadPortfolio(unittest.TestCase):
    def test_loads_sample_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_context(
                tmp, portfolio=read_user_buckets(SAMPLE_ASSETS)
            )
            ctx.load_portfolio()
            data = ctx.portfolio

            self.assertIsInstance(data, dict)
            for bucket in (
                "equity_portfolio",
                "bond_portfolio",
                "fixed_maturity_bond_portfolio",
                "cash_portfolio",
                "commodity_portfolio",
            ):
                self.assertIn(bucket, data)
                self.assertIsInstance(data[bucket], list)

            equity = data["equity_portfolio"]
            self.assertEqual(len(equity), 4)

            amundi_world = equity[0]
            self.assertEqual(amundi_world["name"], "Amundi Core MSCI World UCITS ETF (Acc)")
            self.assertEqual(amundi_world["ISIN"], "IE000BI8OT95")
            self.assertEqual(amundi_world["shares"], 2200)
            self.assertIsNone(amundi_world["value"])
            self.assertEqual(amundi_world["broker"], "scalable")
            self.assertEqual(amundi_world["usavn"], 0.88)

            spdr_midcap = equity[1]
            self.assertEqual(spdr_midcap["ISIN"], "IE00B4YBJ215")
            self.assertEqual(spdr_midcap["value"], 9469.5)
            self.assertEqual(spdr_midcap["dmem"], 1)

            bond = data["bond_portfolio"][0]
            self.assertEqual(bond["ISIN"], "LU2233156582")
            self.assertEqual(bond["shares"], 9000)

            gold = data["commodity_portfolio"][0]
            self.assertEqual(gold["short_name"], "Gold")
            self.assertEqual(gold["shares"], 200)
            self.assertEqual(gold["dmem"], 0.88)


class TestWritePortfolioToDatabase(unittest.TestCase):
    def setUp(self) -> None:
        # Fresh isolated context per test: no shared module state.
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        tmp = Path(self._holder.name)
        self.db = db_path(tmp)
        self.ctx = make_context(
            tmp, portfolio=read_user_buckets(SAMPLE_ASSETS)
        )
        self.ctx.load_portfolio()

    def test_mutations_round_trip_through_disk(self) -> None:
        mutated_value = 12345.67
        mutated_shares = 555

        self.ctx.portfolio["equity_portfolio"][0]["value"] = mutated_value
        self.ctx.portfolio["equity_portfolio"][0]["shares"] = None
        self.ctx.portfolio["bond_portfolio"][0]["shares"] = mutated_shares
        self.ctx.portfolio["commodity_portfolio"][0]["short_name"] = "TestGold"

        self.ctx.persist_portfolio()
        written = read_positions(self.db)

        self.assertEqual(written["equity_portfolio"][0]["value"], mutated_value)
        self.assertIsNone(written["equity_portfolio"][0]["shares"])
        self.assertEqual(written["bond_portfolio"][0]["shares"], mutated_shares)
        self.assertEqual(written["commodity_portfolio"][0]["short_name"], "TestGold")

        untouched = written["equity_portfolio"][1]
        self.assertEqual(untouched["ISIN"], "IE00B4YBJ215")
        self.assertEqual(untouched["value"], 9469.5)
        self.assertEqual(untouched["shares"], 450)


if __name__ == "__main__":
    unittest.main()
