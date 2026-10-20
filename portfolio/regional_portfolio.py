# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from portfolio.portfolio import Portfolio
from cli.logger import attach_color_stderr_handler_for_module

if TYPE_CHECKING:
    from cli.context import RuntimeContext
    from position.position import Position

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


@dataclass
class LabeledPositionGroup:
    short_name: str
    total_value: float
    positions: list[Position]


# Geosplit region keys shared by grouped and ungrouped wedges; grouped
# wedges append ": <short_name>".
_REGIONS = ("Equity US", "Equity Ex-US", "Equity Emrg. Markets")

# Wedges below this portfolio-total fraction are dropped from the chart.
_GEOSPLIT_MIN_WEIGHT = 0.007


class RegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict], ctx: RuntimeContext | None = None):
        super().__init__(name, positions, ctx=ctx)

        if self._value <= 0 and self._positions:
            logger.warning(
                "Regional portfolio %r has zero total value with %d position(s)",
                name,
                len(self._positions),
            )

        # Partition positions into labeled groups and regional positions
        # (membership is construction-time; splits are trusted staged data
        # calculated once here, never refreshed afterwards).
        self._labeled_groups, self._regional_positions = self._partition_and_group_labeled()
        self._build_geo_visualizers()

    def _partition_and_group_labeled(self) -> tuple[list[LabeledPositionGroup], list[Position]]:
        """Split positions into labeled groups (by short_name) and regional positions."""
        labeled_raw = [p for p in self._positions if p._short_name]
        regional = [p for p in self._positions if not p._short_name]

        groups: dict[str, LabeledPositionGroup] = {}
        for pos in labeled_raw:
            key = pos._short_name
            val = pos.value or 0.0
            if key not in groups:
                groups[key] = LabeledPositionGroup(short_name=key, total_value=val, positions=[pos])
            else:
                groups[key].total_value += val
                groups[key].positions.append(pos)

        return list(groups.values()), regional

    @staticmethod
    def _regional_absolute_values(positions: list[Position]) -> tuple[float, float, float]:
        """Absolute (US, Ex-US, EM) values for the given positions.

        Same value-weighted dmem/usavn math the geosplit always used,
        factored so labeled groups and ungrouped positions share it.
        """
        values = np.asarray([p.value for p in positions], dtype=float)
        dmem = np.asarray([p.dmem for p in positions], dtype=float)
        usavn = np.asarray([p.usavn for p in positions], dtype=float)
        developed = float(np.dot(values, dmem))
        us = float(np.dot(values, usavn)) if developed > 0 else 0.0
        subtotal = float(values.sum())
        return us, developed - us, subtotal - developed

    def _compute_geosplit_data(
        self,
        labeled_groups: list[LabeledPositionGroup],
        regional_positions: list[Position],
        total: float,
    ) -> dict[str, float]:
        """Region → portfolio-total fraction, split by Group afterwards.

        Each labeled group is decomposed into regions first (from its own
        positions' dmem/usavn), then emitted under ``"Equity <R>: <group>"``
        keys scaled by its share of the total; group wedges below
        ``_GEOSPLIT_MIN_WEIGHT`` fold into their parent plain region wedge
        instead (25% ``Equity Ex-US`` + 0.5% ``Equity Ex-US: G`` renders as
        25.5% ``Equity Ex-US``). Ungrouped positions feed the plain region
        keys. Keys come out region-major (plain wedge first, then groups
        alphabetically); a region whose folded total still falls below the
        floor is dropped. Folding guarantees a non-empty chart whenever
        the total is positive: all positive mass lands in some region
        total, so one of them always clears the floor.
        """
        if total <= 0:
            return {}
        ordered_groups = sorted(
            (g for g in labeled_groups if g.total_value > 0),
            key=lambda g: g.short_name,
        )
        group_amounts = [
            (g.short_name, self._regional_absolute_values(g.positions))
            for g in ordered_groups
        ]
        plain_amounts = self._regional_absolute_values(regional_positions)
        data: dict[str, float] = {}
        for index, region in enumerate(_REGIONS):
            plain = plain_amounts[index] / total if plain_amounts[index] > 0 else 0.0
            smalls = 0.0
            bigs: list[tuple[str, float]] = []
            for short_name, amounts in group_amounts:
                wedge = amounts[index] / total
                if wedge <= 0:
                    continue
                if wedge >= _GEOSPLIT_MIN_WEIGHT:
                    bigs.append((short_name, wedge))
                else:
                    smalls += wedge
            region_total = plain + smalls
            if region_total >= _GEOSPLIT_MIN_WEIGHT:
                data[region] = region_total
            for short_name, wedge in bigs:
                data[f"{region}: {short_name}"] = wedge
        return data

    def _build_geo_visualizers(self) -> None:
        """Build DMEM/USAVN/geosplit visualizers from construction-time state."""
        plotter = self._ctx.plotter_class()
        labeled_value = sum(g.total_value for g in self._labeled_groups)
        regional_value = self._value - labeled_value

        # DMEM/USAVN from REGIONAL positions only
        if regional_value > 0:
            regional_values = np.asarray([p.value for p in self._regional_positions], dtype=float)
            regional_dmem = np.asarray([p.dmem for p in self._regional_positions], dtype=float)
            regional_usavn = np.asarray([p.usavn for p in self._regional_positions], dtype=float)
            developed_share = float(np.dot(regional_values, regional_dmem)) / regional_value
            dmem_weighted = float(np.dot(regional_values, regional_dmem))
            us_within_developed = (
                float(np.dot(regional_values, regional_usavn)) / dmem_weighted if dmem_weighted > 0 else 0.0
            )
        else:
            developed_share = 0.0
            us_within_developed = 0.0

        if regional_value > 0:
            self._dmem_visualizer = plotter(
                data={
                    "Developed Markets": developed_share,
                    "Emerging Markets": 1.0 - developed_share,
                },
                title="{}: Developed Markets vs. Emerging Markets".format(self._name),
                closing_title="Value: {:.2f}".format(self._value),
            )

            self._usavn_visualizer = plotter(
                data={
                    "US": us_within_developed,
                    "Ex-US": 1.0 - us_within_developed,
                },
                title="{}: US vs. Ex-US (within developed markets)".format(self._name),
                closing_title="Value: {:.2f}".format(self._value),
            )
        else:
            self._dmem_visualizer = None
            self._usavn_visualizer = None

        # Geosplit: regional separation first, then split by Group.
        self._geosplit_data = self._compute_geosplit_data(
            self._labeled_groups, self._regional_positions, self._value
        )

        self._geosplit_visualizer = None
        if self._geosplit_data:
            self._geosplit_visualizer = plotter(
                data=self._geosplit_data,
                title="{}: Regional Split (US vs. Ex-US vs. EM): {:.2f} Euro".format(self._name, self._value),
                closing_title="Value: {:.2f}".format(self._value),
                factor={"value": self._value, "unit": "Euro"},
            )

    def plot_dmem(self) -> None:
        if self._dmem_visualizer is not None:
            self._dmem_visualizer.plot()
        else:
            logger.warning("No dmem visualizer set for portfolio %r; skipping plot", self._name)

    def plot_usavn(self) -> None:
        if self._usavn_visualizer is not None:
            self._usavn_visualizer.plot()
        else:
            logger.warning("No usavn visualizer set for portfolio %r; skipping plot", self._name)

    def __add__(self, other: 'Portfolio') -> 'Portfolio':
        if not isinstance(other, RegionalPortfolio):
            return super().__add__(other)
        merged = object.__new__(RegionalPortfolio)
        merged._ctx = self._ctx
        merged._name = f"{self._name} + {other._name}"
        merged._positions = self._positions + other._positions
        merged._value = self._value + other._value

        # Partition merged positions
        merged._labeled_groups, merged._regional_positions = merged._partition_and_group_labeled()

        merged._dmem = [p.dmem for p in merged._regional_positions]
        merged._usavn = [p.usavn for p in merged._regional_positions]

        # Sectors unchanged (uses all positions)
        merged._sectors = self._merged_sector_union(other, merged._value)
        merged._sector_visualizer = merged._make_sector_visualizer(
            merged._name, merged._value, merged._sector_chart_data()
        )
        logger.debug(
            "RegionalPortfolio %r + %r: merged sectors: %r",
            self._name,
            other._name,
            merged._sectors,
        )

        # Recompute geosplit_data from scratch (shared helper keeps the
        # merged chart consistent with freshly built ones).
        merged._geosplit_data = merged._compute_geosplit_data(
            merged._labeled_groups, merged._regional_positions, merged._value
        )

        # Merge visualizers via PieChart.__add__
        for attr in ("_dmem_visualizer", "_usavn_visualizer", "_geosplit_visualizer"):
            sv, ov = getattr(self, attr, None), getattr(other, attr, None)
            if sv is not None and ov is not None:
                setattr(merged, attr, sv + ov)
            else:
                setattr(merged, attr, sv if sv is not None else ov)
        return merged

    def __str__(self):
        return super().__str__()