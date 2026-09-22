# SPDX-License-Identifier: AGPL-3.0-or-later
"""Split normalization at assembly: Position rows and Portfolio sectors.

Run from repo root::

    pytest tests/test_normalization.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from portfolio.portfolio import (  # noqa: E402
    _normalize_sector_fractions,
    Portfolio,
)
from position.position import normalize_split_rows  # noqa: E402


class TestNormalizeSplitRows(unittest.TestCase):
    def test_rounding_drift_rescaled_to_100(self) -> None:
        rows = [
            {"name": "Japan", "weight_pct": 100.03000000000006},
        ]
        out = normalize_split_rows(rows)
        assert out is not None
        self.assertAlmostEqual(
            sum(float(r["weight_pct"]) for r in out),  # type: ignore[arg-type]
            100.0,
        )
        self.assertEqual(out[0]["name"], "Japan")

    def test_exact_100_passes_through(self) -> None:
        rows = [
            {"name": "France", "weight_pct": 50.0},
            {"name": "Germany", "weight_pct": 50.0},
        ]
        self.assertEqual(normalize_split_rows(rows), rows)

    def test_partial_and_empty_untouched(self) -> None:
        partial = [{"name": "France", "weight_pct": 60.0}]
        self.assertEqual(normalize_split_rows(partial), partial)
        self.assertEqual(normalize_split_rows([]), [])
        self.assertIsNone(normalize_split_rows(None))

    def test_far_out_of_band_untouched(self) -> None:
        rows = [{"name": "France", "weight_pct": 150.0}]
        self.assertEqual(normalize_split_rows(rows), rows)


def _stub(*, value, sectors):
    return SimpleNamespace(
        value=value,
        dmem=1.0,
        usavn=0.5,
        sectors=lambda: sectors,
        _short_name=None,
        _name="stub",
        _isin="XX000STUB00",
    )


def _ctx():
    from context import AppConfig, RuntimeContext

    return RuntimeContext(config=AppConfig(plotter="web"))


class TestNormalizeSectorFractions(unittest.TestCase):
    def test_sums_to_exactly_one(self) -> None:
        self.assertEqual(
            sum(_normalize_sector_fractions({"A": 0.50015, "B": 0.50015}).values()),
            1.0,
        )

    def test_empty_and_unit_untouched(self) -> None:
        self.assertEqual(_normalize_sector_fractions({}), {})
        sectors = {"A": 0.5, "B": 0.5}
        self.assertEqual(_normalize_sector_fractions(sectors), sectors)

    def test_portfolio_sectors_sum_to_one(self) -> None:
        with patch("portfolio.portfolio._factory", side_effect=[
            _stub(
                value=100.0,
                sectors=[{"name": "Technology", "weight_pct": 100.03}],
            )
        ]):
            port = Portfolio("P", [{}], ctx=_ctx())
        self.assertEqual(sum(port.sectors.values()), 1.0)
        self.assertEqual(port.sectors, {"Technology": 1.0})


if __name__ == "__main__":
    unittest.main()
