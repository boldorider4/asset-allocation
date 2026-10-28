# SPDX-License-Identifier: AGPL-3.0-or-later
"""DMEM/USAVN calculation from country rows, without seed bias."""

from __future__ import annotations

import unittest

from cli.context import AppConfig, RuntimeContext
from position.position import Position


class _StubPosition(Position):
    def _fast_info_price(self) -> float | None:
        return None

    def _history_last_close(self) -> float | None:
        return None


def _position(
    *,
    cached_countries: dict[str, float] | None = None,
    dmem: float | None = None,
    usavn: float | None = None,
) -> _StubPosition:
    return _StubPosition(
        "",
        name="stub",
        value=100.0,
        dmem=dmem,
        usavn=usavn,
        cached_countries=cached_countries,
        ctx=RuntimeContext(config=AppConfig()),
    )


class TestMarketSharesFromCountries(unittest.TestCase):
    def test_dmem_and_usavn_have_no_seed_bias(self) -> None:
        pos = _position(
            cached_countries={
                "United States": 0.6237541,
                "France": 0.2500912,
                "China": 0.1261547,
            }
        )
        self.assertAlmostEqual(pos.dmem, 0.8738453)
        self.assertAlmostEqual(pos.usavn, 0.6237541 / 0.8738453)

    def test_all_developed_usavn_has_no_seed_bias(self) -> None:
        pos = _position(
            cached_countries={
                "United States": 0.6,
                "France": 0.4,
            }
        )
        self.assertAlmostEqual(pos.dmem, 1.0)
        self.assertAlmostEqual(pos.usavn, 0.6)

    def test_country_rows_override_stored_fallback(self) -> None:
        pos = _position(
            cached_countries={
                "United States": 0.5,
                "China": 0.5,
            },
            dmem=0.1,
            usavn=0.1,
        )
        self.assertAlmostEqual(pos.dmem, 0.5)
        self.assertAlmostEqual(pos.usavn, 1.0)

    def test_stored_shares_preserved_without_countries(self) -> None:
        pos = _position(dmem=0.75, usavn=0.6)
        self.assertEqual(pos.dmem, 0.75)
        self.assertEqual(pos.usavn, 0.6)

    def test_missing_shares_and_countries_default_to_zero(self) -> None:
        pos = _position()
        self.assertEqual(pos.dmem, 0)
        self.assertEqual(pos.usavn, 0)


if __name__ == "__main__":
    unittest.main()
