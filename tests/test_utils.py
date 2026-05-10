"""Tests for ``utils.load_portfolio`` and ``utils.write_portfolio_to_file``.

Run from repo root::

    pytest tests/test_utils.py -v

The write test deliberately reads the produced file with the stdlib
``json`` module (not ``load_portfolio``) so the round-trip assertion does
not silently confirm a bug shared by the loader.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Make ``utils`` importable regardless of how the test is invoked
# (pytest from a subdir, ``python tests/test_utils.py``, IDE runners, ...).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch  # noqa: E402

from utils import (  # noqa: E402
    apply_incognito_scaling,
    get_incognito_value_factor,
    load_portfolio,
    set_incognito_value_factor,
    write_portfolio_to_file,
)
from utils import portfolio as global_portfolio  # noqa: E402

SAMPLE_ASSETS = REPO_ROOT / "assets.sample.json"


class TestLoadPortfolio(unittest.TestCase):
    def test_loads_sample_assets(self) -> None:
        data = load_portfolio(SAMPLE_ASSETS)

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
        self.assertEqual(amundi_world["shares"], 220)
        self.assertIsNone(amundi_world["value"])
        self.assertEqual(amundi_world["broker"], "scalable")
        self.assertEqual(amundi_world["usavn"], 0.88)

        spdr_midcap = equity[1]
        self.assertEqual(spdr_midcap["ISIN"], "IE00B4YBJ215")
        self.assertEqual(spdr_midcap["value"], 9469.5)
        self.assertEqual(spdr_midcap["dmem"], 1)

        bond = data["bond_portfolio"][0]
        self.assertEqual(bond["ISIN"], "LU2233156582")
        self.assertEqual(bond["shares"], 100)

        gold = data["commodity_portfolio"][0]
        self.assertEqual(gold["short_name"], "Gold")
        self.assertEqual(gold["shares"], 20)
        self.assertEqual(gold["dmem"], 0.88)


class TestWritePortfolioToFile(unittest.TestCase):
    def setUp(self) -> None:
        # Snapshot module-level global so test runs don't leak state.
        self._saved_portfolio = {k: list(v) for k, v in global_portfolio.items()}
        global_portfolio.clear()
        global_portfolio.update(load_portfolio(SAMPLE_ASSETS))

    def tearDown(self) -> None:
        global_portfolio.clear()
        global_portfolio.update(self._saved_portfolio)

    def test_mutations_round_trip_through_disk(self) -> None:
        mutated_value = 12345.67
        mutated_shares = 555

        global_portfolio["equity_portfolio"][0]["value"] = mutated_value
        global_portfolio["equity_portfolio"][0]["shares"] = None
        global_portfolio["bond_portfolio"][0]["shares"] = mutated_shares
        global_portfolio["commodity_portfolio"][0]["short_name"] = "TestGold"

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "assets.json"
            write_portfolio_to_file(out_path)

            self.assertTrue(out_path.exists())

            with out_path.open(encoding="utf-8") as f:
                written = json.load(f)

        self.assertEqual(written["equity_portfolio"][0]["value"], mutated_value)
        self.assertIsNone(written["equity_portfolio"][0]["shares"])
        self.assertEqual(written["bond_portfolio"][0]["shares"], mutated_shares)
        self.assertEqual(written["commodity_portfolio"][0]["short_name"], "TestGold")

        untouched = written["equity_portfolio"][1]
        self.assertEqual(untouched["ISIN"], "IE00B4YBJ215")
        self.assertEqual(untouched["value"], 9469.5)
        self.assertEqual(untouched["shares"], 45)


class TestIncognitoScaling(unittest.TestCase):
    def test_sets_factor_from_explicit_values_without_mutating_dict(self) -> None:
        saved = copy.deepcopy(dict(global_portfolio))
        saved_factor = get_incognito_value_factor()
        try:
            global_portfolio.clear()
            global_portfolio.update(
                {
                    "a": [{"value": 40.0}],
                    "b": [{"value": 60.0}],
                }
            )
            with patch("random.randint", return_value=25000):
                apply_incognito_scaling()
            self.assertEqual(get_incognito_value_factor(), 250.0)
            self.assertAlmostEqual(global_portfolio["a"][0]["value"], 40.0)
            self.assertAlmostEqual(global_portfolio["b"][0]["value"], 60.0)
            raw_total = sum(
                float(p["value"])
                for positions in global_portfolio.values()
                for p in positions
            )
            self.assertEqual(raw_total, 100.0)
        finally:
            global_portfolio.clear()
            global_portfolio.update(copy.deepcopy(saved))
            set_incognito_value_factor(saved_factor)


if __name__ == "__main__":
    unittest.main()
