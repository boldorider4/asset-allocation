from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from portfolio.portfolio import Portfolio
from logger import attach_color_stderr_handler_for_module

if TYPE_CHECKING:
    from context import RuntimeContext

logger = logging.getLogger(__name__)
attach_color_stderr_handler_for_module(logger)

class NonRegionalPortfolio(Portfolio):
    def __init__(self, name: str, positions: list[dict], ctx: RuntimeContext | None = None, consolidate: bool = False):
        super().__init__(name, positions, ctx=ctx)
        plotter = self._ctx.plotter_class()

        if self._value <= 0 and self._positions:
            logger.warning(
                "Non-regional portfolio %r has zero or negative total value with %d position(s)",
                name,
                len(self._positions),
            )

        if consolidate:
            self._visualizer_data = {
                name: (1.0 if self._value > 0 else 0.0),
            }
        else:
            self._visualizer_data = dict()
            for position in self._positions:
                prev_val = 0
                label = position._short_name or position._name
                if label in self._visualizer_data:
                    prev_val = float(self._visualizer_data[label])
                self._visualizer_data[label] = prev_val + float(position.value)

            if self._value > 0:
                inv_total = 1.0 / self._value
                self._visualizer_data = {
                    k: float(v) * inv_total for k, v in self._visualizer_data.items()
                }
            else:
                self._visualizer_data = {k: 0.0 for k in self._visualizer_data}


        self._geosplit_visualizer = plotter(
            data=self._visualizer_data,
            title="{}: {:.2f} Euro".format(name, self._value),
            closing_title="Value: {:.2f}".format(self._value),
            factor={"value": self._value, "unit": "Euro"},
        )

    def _calculate_dmem(self) -> None:
        return None

    def _calculate_usavn(self) -> None:
        return None

    def __str__(self):
        return super().__str__()
