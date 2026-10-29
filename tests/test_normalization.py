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
from position.position import Position, normalize_split_rows  # noqa: E402


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

    def test_negative_under_half_a_percent_is_excluded(self) -> None:
        # DWS holdings API float dust (cf. IE00BLNMYC90 Switzerland
        # -0.000335 weight_pct == -3.3513e-06 fraction): the nonsensical
        # slice is dropped and the remaining rows are renormalized.
        rows = [
            {"name": "United States", "weight_pct": 96.0},
            {"name": "Germany", "weight_pct": 4.000335},
            {"name": "Switzerland", "weight_pct": -0.000335},
        ]
        out = normalize_split_rows(rows)
        assert out is not None
        by_name = {r["name"]: float(r["weight_pct"]) for r in out}  # type: ignore[arg-type]
        self.assertNotIn("Switzerland", by_name)
        for weight in by_name.values():
            self.assertGreaterEqual(weight, 0.0)
        self.assertAlmostEqual(sum(by_name.values()), 100.0)

    def test_negative_slice_below_half_a_percent_is_excluded(self) -> None:
        rows = [
            {"name": "United States", "weight_pct": 99.75},
            {"name": "France", "weight_pct": -0.25},
        ]
        out = normalize_split_rows(rows)
        assert out is not None
        self.assertEqual([r["name"] for r in out], ["United States"])
        self.assertAlmostEqual(float(out[0]["weight_pct"]), 100.0)  # type: ignore[arg-type]

    def test_exact_100_with_dust_still_excluded(self) -> None:
        # Total exactly 100 must not let a negative slice slip through.
        rows = [
            {"name": "United States", "weight_pct": 99.999335},
            {"name": "Germany", "weight_pct": 0.001},
            {"name": "Switzerland", "weight_pct": -0.000335},
        ]
        out = normalize_split_rows(rows)
        assert out is not None
        by_name = {r["name"]: float(r["weight_pct"]) for r in out}  # type: ignore[arg-type]
        self.assertNotIn("Switzerland", by_name)
        self.assertAlmostEqual(sum(by_name.values()), 100.0)

    def test_genuine_negative_untouched(self) -> None:
        # A real negative (e.g. short position) is not dust: it passes
        # through so the strict cache validator still rejects it.
        rows = [
            {"name": "United States", "weight_pct": 105.0},
            {"name": "Switzerland", "weight_pct": -5.0},
        ]
        self.assertEqual(normalize_split_rows(rows), rows)

    def test_position_applies_the_filter_to_countries_and_sectors(self) -> None:
        class _StubPosition(Position):
            def _fast_info_price(self) -> float | None:
                return None

            def _history_last_close(self) -> float | None:
                return None

        pos = _StubPosition(
            "",
            name="stub",
            value=100.0,
            cached_countries={
                "United States": 0.9999,
                "France": -0.0000325345,
            },
            cached_sectors={
                "Technology": 0.9999,
                "Finance": -0.0000325345,
            },
            ctx=_ctx(),
        )
        self.assertEqual([r["name"] for r in pos.countries()], ["United States"])
        self.assertEqual([r["name"] for r in pos.sectors() or []], ["Technology"])
        self.assertAlmostEqual(float(pos.countries()[0]["weight_pct"]), 100.0)  # type: ignore[arg-type]
        self.assertAlmostEqual(
            float((pos.sectors() or [])[0]["weight_pct"]),  # type: ignore[index]
            100.0,
        )

    def test_float_dust_above_one_stages_into_cache(self) -> None:
        # The CH0016999846 failure shape: 99.99 normalizes to
        # 100.00000000000001, which becomes 1.0000000000000002 as a
        # fraction. The cache boundary must clamp and snap it to 1.
        from storage.records import CacheEntry

        rows = [{"name": "Switzerland", "weight_pct": 99.99}]
        staged = normalize_split_rows(rows)
        fractions = CacheEntry.rows_to_fractions(staged)
        entry = CacheEntry("CH0016999846", {"countries": fractions})
        parsed = entry.to_dict()["countries"]
        self.assertEqual(parsed, {"Switzerland": 1.0})
        self.assertEqual(sum(parsed.values()), 1.0)

    def test_filtered_rows_stage_into_cache(self) -> None:
        # End to end: scraped rows with a nonsensical negative slice must
        # survive the rows_to_fractions -> CacheEntry validation boundary.
        from storage.records import CacheEntry

        rows = [
            {"name": "United States", "weight_pct": 96.0},
            {"name": "Germany", "weight_pct": 4.000335},
            {"name": "Switzerland", "weight_pct": -0.000335},
        ]
        staged = normalize_split_rows(rows)
        fractions = CacheEntry.rows_to_fractions(staged)
        entry = CacheEntry("IE00BLNMYC90", {"countries": fractions})
        parsed = entry.to_dict()["countries"]
        self.assertNotIn("Switzerland", parsed)
        self.assertAlmostEqual(sum(parsed.values()), 1.0)


class TestOvershootTolerance(unittest.TestCase):
    """Vendor overshoot (e.g. iShares reporting Japan at 101.47 with a
    101.48 total for IE00BFNM3L97): rescale with a warning within
    (101, 102], pass through beyond, never touch under-coverage."""

    def test_overshoot_rescaled_with_warning(self) -> None:
        rows = [
            {"name": "Japan", "weight_pct": 101.47},
            {"name": "United Kingdom", "weight_pct": 0.01},
        ]
        with self.assertLogs("position.position", level="WARNING") as logs:
            out = normalize_split_rows(rows)
        self.assertTrue(any("overshooting" in line for line in logs.output))
        assert out is not None
        by_name = {r["name"]: float(r["weight_pct"]) for r in out}  # type: ignore[arg-type]
        self.assertAlmostEqual(sum(by_name.values()), 100.0)
        self.assertLess(by_name["Japan"], 100.0)

    def test_overshoot_boundary_102_rescales(self) -> None:
        rows = [{"name": "Japan", "weight_pct": 102.0}]
        with self.assertLogs("position.position", level="WARNING"):
            out = normalize_split_rows(rows)
        assert out is not None
        self.assertAlmostEqual(float(out[0]["weight_pct"]), 100.0)  # type: ignore[arg-type]

    def test_beyond_overshoot_untouched(self) -> None:
        rows = [
            {"name": "Japan", "weight_pct": 102.4},
            {"name": "United Kingdom", "weight_pct": 0.1},
        ]
        self.assertEqual(normalize_split_rows(rows), rows)

    def test_under_coverage_still_untouched(self) -> None:
        # Overshoot-only widening: the lower side stays frozen at the
        # quiet band so partial tables are never inflated to look whole.
        rows = [{"name": "France", "weight_pct": 98.5}]
        self.assertEqual(normalize_split_rows(rows), rows)

    def test_quiet_band_stays_quiet(self) -> None:
        rows = [
            {"name": "Japan", "weight_pct": 100.4},
            {"name": "United Kingdom", "weight_pct": 0.1},
        ]
        with self.assertNoLogs("position.position", level="WARNING"):
            out = normalize_split_rows(rows)
        assert out is not None
        self.assertAlmostEqual(
            sum(float(r["weight_pct"]) for r in out),  # type: ignore[arg-type]
            100.0,
        )

    def test_overshoot_rows_stage_into_cache(self) -> None:
        # End to end: the exact IE00BFNM3L97 failure shape must survive
        # the rows_to_fractions -> CacheEntry validation boundary.
        from storage.records import CacheEntry

        rows = [
            {"name": "Japan", "weight_pct": 101.47},
            {"name": "United Kingdom", "weight_pct": 0.01},
        ]
        staged = normalize_split_rows(rows)
        fractions = CacheEntry.rows_to_fractions(staged)
        entry = CacheEntry("IE00BFNM3L97", {"countries": fractions})
        parsed = entry.to_dict()["countries"]
        self.assertLess(parsed["Japan"], 1.0)
        self.assertAlmostEqual(sum(parsed.values()), 1.0)


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
    from cli.context import AppConfig, RuntimeContext

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
