from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from portfolio.portfolio import Portfolio
from logger import attach_color_stderr_handler_for_module

if TYPE_CHECKING:
    from context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

class RegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict], ctx: RuntimeContext | None = None):
        super().__init__(name, positions, ctx=ctx)
        plotter = self._ctx.plotter_class()

        if self._value <= 0 and self._positions:
            logger.warning(
                "Regional portfolio %r has zero total value with %d position(s)",
                name,
                len(self._positions),
            )

        values = np.asarray([position.value for position in self._positions], dtype=float)
        dmem_arr = np.asarray(self._dmem, dtype=float)
        usavn_arr = np.asarray(self._usavn, dtype=float)
        developed_share = (
            float(np.dot(values, dmem_arr)) / self._value if self._value > 0 else 0.0
        )
        dmem_weighted = float(np.dot(values, dmem_arr))
        us_within_developed = (
            float(np.dot(values, usavn_arr)) / dmem_weighted if dmem_weighted > 0 else 0.0
        )

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

        # now let's look at regional split: us vs. ex-us vs. emerging markets
        # Scale us_within_developed by the developed_share so that US is proportional to the total_value
        self._geosplit_data = {
            "Equity US": us_within_developed * developed_share,
            "Equity Ex-US": (1.0 - us_within_developed) * developed_share,
            "Equity Emrg. Markets": 1.0 - developed_share,
        }
        self._geosplit_visualizer = plotter(
            data=self._geosplit_data,
            title="{}: Regional Split (US vs. Ex-US vs. EM): {:.2f} Euro".format(self._name, self._value),
            closing_title="Value: {:.2f}".format(self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    def plot_dmem(self) -> None:
        self._dmem_visualizer.plot()

    def plot_usavn(self) -> None:
        self._usavn_visualizer.plot()

    def __add__(self, other: 'Portfolio') -> 'Portfolio':
        if not isinstance(other, RegionalPortfolio):
            return super().__add__(other)
        merged = object.__new__(RegionalPortfolio)
        merged._ctx = self._ctx
        merged._name = f"{self._name} + {other._name}"
        merged._positions = self._positions + other._positions
        merged._value = self._value + other._value
        merged._dmem = list(self._dmem or []) + list(other._dmem or [])
        merged._usavn = list(self._usavn or []) + list(other._usavn or [])
        # Plain associative union: every position's mass enters exactly once,
        # at its home portfolio via rows or constituent wedges.
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
        total = merged._value
        keys = self._geosplit_data.keys() | other._geosplit_data.keys()
        merged._geosplit_data = {
            k: (
                self._value * self._geosplit_data.get(k, 0.0)
                + other._value * other._geosplit_data.get(k, 0.0)
            ) / total
            if total > 0 else 0.0
            for k in keys
        }
        for attr in ("_dmem_visualizer", "_usavn_visualizer", "_geosplit_visualizer"):
            sv, ov = getattr(self, attr, None), getattr(other, attr, None)
            if sv is not None and ov is not None:
                setattr(merged, attr, sv + ov)
            else:
                setattr(merged, attr, sv if sv is not None else ov)
        return merged

    def __str__(self):
        return super().__str__()
