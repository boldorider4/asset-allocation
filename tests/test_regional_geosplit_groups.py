# SPDX-License-Identifier: AGPL-3.0-or-later
"""RegionalPortfolio geosplit: regional split first, Group split afterwards.

Labeled groups (short_name) decompose into regions from their own
positions' dmem/usavn and render under "Equity <R>: <group>" keys;
ungrouped positions keep the plain region keys. Covers construction,
merge (__add__), and zero-value handling.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from portfolio.regional_portfolio import RegionalPortfolio


def _stub(*, value, dmem=1.0, usavn=0.5, short_name=None, sectors=None):
    return SimpleNamespace(
        value=value,
        dmem=dmem,
        usavn=usavn,
        sectors=lambda: sectors,
        _short_name=short_name,
        _name="stub",
        _isin="XX000STUB00",
    )


def _ctx():
    from cli.context import AppConfig, RuntimeContext

    return RuntimeContext(config=AppConfig(plotter="web"))


def _regional(name, stubs) -> RegionalPortfolio:
    with patch("portfolio.portfolio._factory", side_effect=list(stubs)):
        return RegionalPortfolio(name, [{} for _ in stubs], ctx=_ctx())


class TestUngroupedKeysUnchanged(unittest.TestCase):
    def test_plain_keys_and_zero_dropped(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=1.0, usavn=1.0),
                _stub(value=100.0, dmem=0.0, usavn=0.0),
            ],
        )
        self.assertEqual(
            port._geosplit_data, {"Equity US": 0.5, "Equity Emrg. Markets": 0.5}
        )

    def test_sums_to_one(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=1.0, usavn=0.5),
                _stub(value=300.0, dmem=0.5, usavn=0.25),
            ],
        )
        self.assertAlmostEqual(sum(port._geosplit_data.values()), 1.0)


class TestGroupedSplit(unittest.TestCase):
    def test_single_group_splits_by_region(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=1.0, usavn=1.0, short_name="World"),
                _stub(value=100.0, dmem=0.0, usavn=0.0, short_name="World"),
            ],
        )
        self.assertEqual(
            port._geosplit_data,
            {"Equity US: World": 0.5, "Equity Emrg. Markets: World": 0.5},
        )

    def test_group_split_uses_member_regions(self) -> None:
        # The EM-only group must not inherit the global US share: regions
        # come from each group's own positions, groups only label them.
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=0.0, usavn=0.0, short_name="G"),
                _stub(value=100.0, dmem=1.0, usavn=1.0),
            ],
        )
        self.assertEqual(
            port._geosplit_data,
            {"Equity Emrg. Markets: G": 0.5, "Equity US": 0.5},
        )

    def test_mixed_grouped_and_ungrouped_sum_to_one(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=1.0, usavn=0.0, short_name="G"),
                _stub(value=100.0, dmem=1.0, usavn=1.0, short_name="H"),
                _stub(value=200.0, dmem=0.0, usavn=0.0),
            ],
        )
        self.assertEqual(
            port._geosplit_data,
            {
                "Equity Ex-US: G": 0.25,
                "Equity US: H": 0.25,
                "Equity Emrg. Markets": 0.5,
            },
        )
        self.assertAlmostEqual(sum(port._geosplit_data.values()), 1.0)

    def test_zero_value_group_emits_nothing(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=0.0, dmem=1.0, usavn=1.0, short_name="Zero"),
                _stub(value=100.0, dmem=1.0, usavn=1.0),
            ],
        )
        self.assertEqual(port._geosplit_data, {"Equity US": 1.0})


class TestKeyOrdering(unittest.TestCase):
    def test_region_major_with_plain_first(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=100.0, dmem=1.0, usavn=1.0),
                _stub(value=100.0, dmem=1.0, usavn=1.0, short_name="Zebra"),
                _stub(value=100.0, dmem=1.0, usavn=1.0, short_name="Alpha"),
                _stub(value=100.0, dmem=1.0, usavn=0.0, short_name="B"),
            ],
        )
        self.assertEqual(
            list(port._geosplit_data.keys()),
            ["Equity US", "Equity US: Alpha", "Equity US: Zebra", "Equity Ex-US: B"],
        )
        self.assertEqual(
            list(port._geosplit_data.values()), [0.25, 0.25, 0.25, 0.25]
        )


class TestMinWeightFloor(unittest.TestCase):
    def test_wedges_below_floor_dropped(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=9930.0, dmem=1.0, usavn=1.0),
                _stub(value=60.0, dmem=0.0, usavn=0.0, short_name="Tiny"),
            ],
        )
        # 60 / 9990 < 0.007: dropped, and the mass is not reallocated.
        self.assertEqual(list(port._geosplit_data.keys()), ["Equity US"])
        self.assertAlmostEqual(
            port._geosplit_data["Equity US"], 9930.0 / 9990.0
        )

    def test_wedge_at_floor_kept(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=9930.0, dmem=1.0, usavn=1.0),
                _stub(value=70.0, dmem=0.0, usavn=0.0, short_name="Edge"),
            ],
        )
        # 70 / 10000 == 0.007: kept (>=).
        self.assertEqual(
            list(port._geosplit_data.keys()), ["Equity US", "Equity Emrg. Markets: Edge"]
        )

    def test_subfloor_groups_fold_into_parent(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=745.0, dmem=1.0, usavn=1.0),
                _stub(value=250.0, dmem=1.0, usavn=0.0),
                _stub(value=5.0, dmem=1.0, usavn=0.0, short_name="G"),
            ],
        )
        # 25% plain Ex-US + 0.5% grouped Ex-US renders as 25.5% plain.
        self.assertEqual(
            list(port._geosplit_data.keys()), ["Equity US", "Equity Ex-US"]
        )
        self.assertAlmostEqual(port._geosplit_data["Equity US"], 0.745)
        self.assertAlmostEqual(port._geosplit_data["Equity Ex-US"], 0.255)

    def test_folded_smalls_can_clear_floor_without_plain(self) -> None:
        port = _regional(
            "Equity",
            [
                _stub(value=992.0, dmem=1.0, usavn=1.0),
                _stub(value=4.0, dmem=0.0, usavn=0.0, short_name="A"),
                _stub(value=4.0, dmem=0.0, usavn=0.0, short_name="B"),
            ],
        )
        # Neither 0.004 wedge clears the floor alone, but their folded
        # 0.008 region total does — under the plain key.
        self.assertEqual(
            list(port._geosplit_data.keys()), ["Equity US", "Equity Emrg. Markets"]
        )
        self.assertAlmostEqual(
            port._geosplit_data["Equity Emrg. Markets"], 0.008
        )

    def test_many_subfloor_groups_fold_to_parent(self) -> None:
        stubs = [
            _stub(value=1.0, dmem=1.0, usavn=1.0, short_name=f"G{i:03d}")
            for i in range(150)
        ]
        port = _regional("Equity", stubs)
        # Every wedge is 1/150 < 0.007: all fold into the parent key.
        self.assertEqual(list(port._geosplit_data.keys()), ["Equity US"])
        self.assertAlmostEqual(port._geosplit_data["Equity US"], 1.0)


class TestGroupedMerge(unittest.TestCase):
    def test_shared_group_label_sums(self) -> None:
        left = _regional(
            "Equity", [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")]
        )
        right = _regional(
            "Other", [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")]
        )
        merged = left + right
        self.assertIsInstance(merged, RegionalPortfolio)
        self.assertEqual(merged._geosplit_data, {"Equity US: G": 1.0})

    def test_distinct_groups_union(self) -> None:
        left = _regional(
            "Equity", [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")]
        )
        right = _regional(
            "Other", [_stub(value=100.0, dmem=0.0, usavn=0.0, short_name="H")]
        )
        merged = left + right
        self.assertEqual(
            merged._geosplit_data,
            {"Equity US: G": 0.5, "Equity Emrg. Markets: H": 0.5},
        )


class TestRegionPrefix(unittest.TestCase):
    def test_prefix_comes_from_the_portfolio_name(self) -> None:
        port = _regional(
            "Bonds",
            [
                _stub(value=100.0, dmem=1.0, usavn=1.0),
                _stub(value=100.0, dmem=0.0, usavn=0.0),
            ],
        )
        self.assertEqual(
            port._geosplit_data,
            {"Bonds US": 0.5, "Bonds Emrg. Markets": 0.5},
        )

    def test_grouped_wedges_carry_the_prefix_too(self) -> None:
        port = _regional(
            "Bonds",
            [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")],
        )
        self.assertEqual(port._geosplit_data, {"Bonds US: G": 1.0})

    def test_merge_keeps_the_base_prefix(self) -> None:
        # The merged name is only for display: the wedge labels keep the
        # prefix of the base portfolio.
        left = _regional(
            "Equity", [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")]
        )
        right = _regional(
            "Other", [_stub(value=100.0, dmem=1.0, usavn=1.0, short_name="G")]
        )
        merged = left + right
        self.assertEqual(merged._name, "Equity + Other")
        self.assertEqual(merged._geosplit_data, {"Equity US: G": 1.0})


if __name__ == "__main__":
    unittest.main()
