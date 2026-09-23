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

from context import AppConfig, RuntimeContext  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
