# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from portfolio.portfolio import Portfolio, LabeledPositionGroup
from logger import attach_color_stderr_handler_for_module

if TYPE_CHECKING:
    from context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)


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
        # (membership is construction-time; values are object snapshots —
        # refreshes rebuild derived artifacts, never the partition itself).
        self._labeled_groups, self._regional_positions = self._partition_and_group_labeled()
        self._build_geo_visualizers()

    def _build_geo_visualizers(self) -> None:
        """(Re)build DMEM/USAVN/geosplit visualizers from current state.

        Shared by construction and ``refresh_countries`` so the two can
        never drift apart.
        """
        plotter = self._ctx.plotter_class()
        labeled_value = sum(g.total_value for g in self._labeled_groups)
        regional_value = self._value - labeled_value
        scale_regional = regional_value / self._value if self._value > 0 else 0.0

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

        # Geosplit: labeled position groups first, then regional wedges (scaled)
        self._geosplit_data = {}
        for group in self._labeled_groups:
            if group.total_value > 0:
                self._geosplit_data[group.short_name] = group.total_value / self._value
        self._geosplit_data.update({
            "Equity US": us_within_developed * developed_share * scale_regional,
            "Equity Ex-US": (1.0 - us_within_developed) * developed_share * scale_regional,
            "Equity Emrg. Markets": (1.0 - developed_share) * scale_regional,
        })

        self._geosplit_visualizer = plotter(
            data=self._geosplit_data,
            title="{}: Regional Split (US vs. Ex-US vs. EM): {:.2f} Euro".format(self._name, self._value),
            closing_title="Value: {:.2f}".format(self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    def refresh_countries(self) -> None:
        """Refresh geo state after a holdings update.

        Heals positions missing countries (see ``Position.refresh_geo``),
        recomputes the DMEM/USAVN aggregates, and rebuilds the geo
        visualizers so merged charts pick up the fresh data.
        """
        super().refresh_countries()
        self._build_geo_visualizers()

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

        # DMEM/USAVN from regional positions only
        labeled_value = sum(g.total_value for g in merged._labeled_groups)
        regional_value = merged._value - labeled_value
        if regional_value > 0:
            regional_values = np.asarray([p.value for p in merged._regional_positions], dtype=float)
            regional_dmem = np.asarray([p.dmem for p in merged._regional_positions], dtype=float)
            regional_usavn = np.asarray([p.usavn for p in merged._regional_positions], dtype=float)
            developed_share = float(np.dot(regional_values, regional_dmem)) / regional_value
            dmem_weighted = float(np.dot(regional_values, regional_dmem))
            us_within_developed = (
                float(np.dot(regional_values, regional_usavn)) / dmem_weighted if dmem_weighted > 0 else 0.0
            )
        else:
            developed_share = 0.0
            us_within_developed = 0.0

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

        # Recompute geosplit_data from scratch
        scale_regional = regional_value / merged._value if merged._value > 0 else 0.0
        merged._geosplit_data = {}
        for group in merged._labeled_groups:
            if group.total_value > 0:
                merged._geosplit_data[group.short_name] = group.total_value / merged._value
        merged._geosplit_data.update({
            "Equity US": us_within_developed * developed_share * scale_regional,
            "Equity Ex-US": (1.0 - us_within_developed) * developed_share * scale_regional,
            "Equity Emrg. Markets": (1.0 - developed_share) * scale_regional,
        })

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