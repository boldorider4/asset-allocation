# SPDX-License-Identifier: AGPL-3.0-or-later
"""``Position.invalidate_sectors``: sectors-only invalidation.

Splits are staged per field: invalidating must clear staged sector rows
(so they refetch from JustETF on next access) while leaving countries
(and the DMEM/USAVN derived from them at construction) untouched —
there is no countries refresh path by design.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.context import AppConfig, RuntimeContext  # noqa: E402
from position.justetf_position import JustETFPosition  # noqa: E402

ISIN = "XX000UNKNOWN1"


def _position() -> JustETFPosition:
    ctx = RuntimeContext(config=AppConfig())
    return JustETFPosition(
        ISIN,
        name="Unknown",
        shares=2,
        value=20.0,
        broker="scalable",
        cached_countries={"United States": 0.9},
        cached_sectors={"Technology": 0.5},
        price=10.0,
        ctx=ctx,
    )


def _bare_position() -> JustETFPosition:
    ctx = RuntimeContext(config=AppConfig())
    return JustETFPosition(
        ISIN,
        name="Unknown",
        shares=2,
        value=20.0,
        broker="scalable",
        dmem=0.6,
        usavn=0.3,
        price=10.0,
        ctx=ctx,
    )


class TestInvalidateSectors(unittest.TestCase):
    def test_clears_sectors_only(self) -> None:
        pos = _position()
        dmem, usavn = pos.dmem, pos.usavn
        pos.invalidate_sectors()
        self.assertIsNone(pos._sectors)
        self.assertEqual(
            pos._countries,
            [{"name": "United States", "weight_pct": 90.0}],
        )
        self.assertEqual(pos.dmem, dmem)
        self.assertEqual(pos.usavn, usavn)

    def test_sectors_refetch_on_next_access(self) -> None:
        pos = _position()
        pos.invalidate_sectors()
        fresh = [{"name": "Finance", "weight_pct": 100.0}]
        with patch.object(
            JustETFPosition, "_fetch_sectors_with_retries", return_value=fresh
        ) as fetch_sectors:
            rows = pos.sectors()
        self.assertEqual(rows, fresh)
        fetch_sectors.assert_called_once_with()

    def test_no_countries_refetch(self) -> None:
        pos = _position()
        pos.invalidate_sectors()
        with (
            patch.object(
                JustETFPosition,
                "_fetch_countries_with_retries",
                side_effect=AssertionError("must not scrape countries"),
            ),
            patch.object(
                JustETFPosition,
                "_fetch_sectors_with_retries",
                return_value=[{"name": "Finance", "weight_pct": 100.0}],
            ),
        ):
            pos.sectors()
            self.assertEqual(
                pos._countries,
                [{"name": "United States", "weight_pct": 90.0}],
            )


class TestRefreshGeo(unittest.TestCase):
    def test_miss_heals_and_recomputes_exactly(self) -> None:
        pos = _bare_position()
        self.assertIsNone(pos._countries)
        self.assertAlmostEqual(pos.dmem, 0.6)
        self.assertAlmostEqual(pos.usavn, 0.3)
        fresh = [
            {"name": "United States", "weight_pct": 90.0},
            {"name": "Germany", "weight_pct": 10.0},
        ]
        with patch.object(
            JustETFPosition, "_fetch_countries_with_retries", return_value=fresh
        ) as fetch:
            self.assertTrue(pos.refresh_geo())
        fetch.assert_called_once_with()
        # Exact recompute from the asset baselines (no double-add):
        # developed = 0.6 + 90 + 10, emerging = 0.4, total = 101.
        self.assertAlmostEqual(pos.dmem, 100.6 / 101.0)
        # us = 0.3 + 90 (US), non_us = 0.7 + 10 (Germany developed).
        self.assertAlmostEqual(pos.usavn, 90.3 / 101.0)

    def test_empty_rows_heal_like_missing(self) -> None:
        # The factory seeds {} (not None) when the cache has no entry;
        # that must heal exactly like a missing cache.
        pos = _bare_position()
        pos._countries = []
        fresh = [
            {"name": "United States", "weight_pct": 90.0},
            {"name": "Germany", "weight_pct": 10.0},
        ]
        with patch.object(
            JustETFPosition, "_fetch_countries_with_retries", return_value=fresh
        ):
            self.assertTrue(pos.refresh_geo())
        self.assertAlmostEqual(pos.dmem, 100.6 / 101.0)
        self.assertAlmostEqual(pos.usavn, 90.3 / 101.0)

    def test_populated_rows_never_recomputed(self) -> None:
        # Guards the double-add trap: a second refresh over staged rows
        # must neither fetch nor touch DMEM/USAVN.
        pos = _position()
        dmem, usavn = pos.dmem, pos.usavn
        with patch.object(
            JustETFPosition,
            "_fetch_countries_with_retries",
            side_effect=AssertionError("must not refetch"),
        ):
            self.assertFalse(pos.refresh_geo())
        self.assertEqual(pos.dmem, dmem)
        self.assertEqual(pos.usavn, usavn)

    def test_fetch_failure_keeps_fallbacks(self) -> None:
        pos = _bare_position()
        with patch.object(
            JustETFPosition,
            "_fetch_countries_with_retries",
            side_effect=RuntimeError("offline"),
        ):
            self.assertFalse(pos.refresh_geo())
        self.assertEqual(pos._countries, [])
        self.assertAlmostEqual(pos.dmem, 0.6)
        self.assertAlmostEqual(pos.usavn, 0.3)


if __name__ == "__main__":
    unittest.main()
