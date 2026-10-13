# SPDX-License-Identifier: AGPL-3.0-or-later
"""Label-aware wedge colors: family classification, ramps, fallback."""

from __future__ import annotations

import colorsys
import unittest

from visual.plot.family_palette import FALLBACK_PALETTE, family_colors


def _hue_degrees(color: str) -> tuple[float, float]:
    r, g, b = (int(color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
    return hue * 360.0, saturation


class TestClassification(unittest.TestCase):
    def test_equity_matches_at_start_or_after_space(self) -> None:
        colors = family_colors(["Equity US", "My Equity Fund", "EQUITY"])
        by_label = dict(zip(["Equity US", "My Equity Fund", "EQUITY"], colors))
        # Plain region wedge ranks greenest; grouped wedges follow along
        # the green -> yellow -> red ramp.
        self.assertEqual(by_label["Equity US"], "#1a9850")
        self.assertEqual(by_label["EQUITY"], "#fee08b")
        self.assertEqual(by_label["My Equity Fund"], "#d73027")

    def test_plain_region_order_beats_alphabet(self) -> None:
        colors = family_colors(
            ["Equity Emrg. Markets", "Equity US", "Equity Ex-US"]
        )
        by_label = dict(
            zip(["Equity Emrg. Markets", "Equity US", "Equity Ex-US"], colors)
        )
        # Canonical region order (US, Ex-US, EM), not alphabetical.
        self.assertEqual(by_label["Equity US"], "#1a9850")
        self.assertEqual(by_label["Equity Ex-US"], "#fee08b")
        self.assertEqual(by_label["Equity Emrg. Markets"], "#d73027")

    def test_no_match_without_boundary(self) -> None:
        colors = family_colors(["MyEquity", "US"])
        self.assertEqual(colors, [FALLBACK_PALETTE[0], FALLBACK_PALETTE[1]])

    def test_bond_and_gold_case_insensitive(self) -> None:
        colors = family_colors(["Bonds", "bond fund", "Gold", "GOLD bars"])
        blue_hues = [_hue_degrees(c)[0] for c in colors[:2]]
        self.assertTrue(all(190.0 <= h <= 270.0 for h in blue_hues), colors)
        self.assertEqual(colors[2], colors[3])
        gold_hue, gold_sat = _hue_degrees(colors[2])
        self.assertGreater(gold_sat, 0.15)
        self.assertTrue(38.0 <= gold_hue <= 75.0, colors[2])

    def test_first_match_wins_in_equity_bond_gold_order(self) -> None:
        (equity_blend,) = family_colors(["Equity Bond Blend"])
        (bond_gold,) = family_colors(["Bond Gold Mix"])
        equity_hue, _ = _hue_degrees(equity_blend)
        bond_hue, _ = _hue_degrees(bond_gold)
        self.assertTrue(80.0 <= equity_hue <= 165.0, equity_blend)
        self.assertTrue(190.0 <= bond_hue <= 270.0, bond_gold)


class TestRamps(unittest.TestCase):
    def test_shades_progress_green_to_red_alphabetically(self) -> None:
        labels = ["Equity Zeta", "Equity Mid", "Equity Alpha", "Equity Kappa"]
        colors = family_colors(labels)
        self.assertEqual(len(set(colors)), 4)
        # Grouped wedges rank alphabetically regardless of input order,
        # walking green -> yellow -> red.
        by_label = dict(zip(labels, colors))
        ordered = [by_label[name] for name in sorted(labels)]
        hues = [_hue_degrees(c)[0] for c in ordered]
        self.assertTrue(80.0 <= hues[0] <= 165.0, ordered[0])
        self.assertEqual(hues, sorted(hues, reverse=True))
        self.assertLess(hues[-1], 40.0)

    def test_deterministic_across_calls(self) -> None:
        labels = ["Equity US: B", "Cash", "Equity US: A", "Bonds"]
        self.assertEqual(family_colors(labels), family_colors(labels))

    def test_single_member_family(self) -> None:
        (only,) = family_colors(["Equity Solo"])
        hue, saturation = _hue_degrees(only)
        self.assertGreater(saturation, 0.15)
        self.assertTrue(80.0 <= hue <= 165.0, only)

    def test_gold_shares_one_tint(self) -> None:
        colors = family_colors(["Gold", "Equity US", "Gold Bars"])
        self.assertEqual(colors[0], colors[2])
        self.assertNotEqual(colors[0], colors[1])

    def test_fallback_cycles_in_payload_order(self) -> None:
        labels = [f"Other {i}" for i in range(len(FALLBACK_PALETTE) + 2)]
        colors = family_colors(labels)
        self.assertEqual(
            colors,
            list(FALLBACK_PALETTE) + list(FALLBACK_PALETTE[:2]),
        )

    def test_mixed_families_keep_positions(self) -> None:
        colors = family_colors(["Cash", "Equity B", "Bonds", "Equity A"])
        again = family_colors(["Equity A", "Equity B"])
        self.assertEqual(colors[1], again[1])
        self.assertEqual(colors[3], again[0])
        self.assertEqual(colors[0], FALLBACK_PALETTE[0])
        self.assertEqual(colors[2], family_colors(["Bonds"])[0])

    def test_empty_input(self) -> None:
        self.assertEqual(family_colors([]), [])


if __name__ == "__main__":
    unittest.main()
