from __future__ import annotations

from copy import deepcopy
from typing import TypedDict
from uuid import uuid4

from visual import Visual


class _PieFactor(TypedDict):
    value: float | int
    unit: str

import matplotlib.pyplot as plt


class PieChart(Visual):
    """Pie chart from label → weight; slice areas match each weight’s share of the total."""

    def __init__(
        self,
        data: dict[str, float],
        title: str | None = None,
        *,
        factor: _PieFactor | None = None,
    ):
        super().__init__(data=data, title=title)
        self._factor = factor

    def _merge_weights(self, other: PieChart) -> tuple[float, float] | None:
        """Return (left, right) portfolio weights for weighted merge, or None for plain additive merge."""
        sf, of = self._factor, other._factor
        if sf is None and of is None:
            return None
        if sf is not None and of is not None and sf["unit"] != of["unit"]:
            return None
        left = float(sf["value"]) if sf is not None else float(sum(self._data.values()))
        right = float(of["value"]) if of is not None else float(sum(other._data.values()))
        return (left, right)

    def __add__(self, other: object) -> PieChart:
        if not isinstance(other, PieChart):
            return NotImplemented

        weights = self._merge_weights(other)
        if weights is None:
            merged: dict[str, float] = deepcopy(self._data)
            for k, v in other._data.items():
                merged[k] = merged.get(k, 0) + v
        else:
            left, right = weights
            total_w = left + right
            keys = set(self._data) | set(other._data)
            merged = {
                k: (self._data.get(k, 0) * left + other._data.get(k, 0) * right) / total_w
                for k in keys
            }

        title_parts: list[str] = []
        if self._title:
            title_parts.append(self._title)
        if other._title:
            title_parts.append(other._title)
        merged_title = " + ".join(title_parts) if title_parts else None

        merged_factor = self._merge_factor_with(other, weights)
        return PieChart(data=merged, title=merged_title, factor=merged_factor)

    def _merge_factor_with(self, other: PieChart, weights: tuple[float, float] | None) -> _PieFactor | None:
        if weights is None:
            return None
        left, right = weights
        sf, of = self._factor, other._factor
        unit = (sf or of)["unit"]
        return {"value": left + right, "unit": unit}

    def plot(self) -> None:
        if not self._data:
            raise ValueError("data must contain at least one entry")

        labels = list(self._data.keys())
        sizes = [float(self._data[k]) for k in labels]

        if any(s < 0 for s in sizes):
            raise ValueError("weights must be non-negative")

        total = sum(sizes)
        if total <= 0:
            raise ValueError("sum of weights must be positive")

        # Do not use title as matplotlib figure num: the same string reuses one figure and
        # stacks new axes, so two PieCharts with the same title look like one broken window.
        fig, ax = plt.subplots(num=str(uuid4()))

        if self._factor is not None:
            total_attr = float(self._factor["value"])
            unit = self._factor["unit"]

            def autopct(pct: float) -> str:
                # pct is wedge share in percent (matplotlib); portions sum to total_attr.
                portion = pct / 100 * total_attr
                return f"{pct:.1f}%\n{portion:.2f} {unit}"

            autopct_arg = autopct
        else:
            autopct_arg = "%1.1f%%"

        ax.pie(
            sizes,
            labels=labels,
            autopct=autopct_arg,
            startangle=90,
        )
        ax.axis("equal")
        if self._title is not None:
            fig.suptitle(self._title)
        # Non-blocking so multiple charts each get their own window.
        plt.show(block=False)
        # Position/size after show so the native window exists (stagger is no-op otherwise).
        self._stagger_figure_window(fig)
        if self._title is not None:
            mgr = fig.canvas.manager
            if mgr is not None:
                setter = getattr(mgr, "set_window_title", None)
                if callable(setter):
                    try:
                        setter(self._title)
                    except Exception:
                        pass
        plt.pause(0.02)


if __name__ == "__main__":
    # sample_0 = {
    #     "USA": 45,
    #     "Emerging Markets": 23,
    #     "ex-USA-Developed": 22,
    # }
    # pc0 = PieChart(data=sample_0, title="Regional split")
    # pc0.plot()

    # sample_1 = {
    #     "Europe": 45,
    #     "Developed Markets": 23,
    #     "Emerging Markets": 11,
    # }
    # pc1 = PieChart(data=sample_1, title="Other Regional split")
    # pc1.plot()

    sample_2 = {
        "Europe": 45,
        "Developed Markets": 23,
        "Emerging Markets": 32,
    }
    factor_2 = {
        "value": 100000,
        "unit": "USD",
    }
    pc2 = PieChart(data=sample_2, title="Labeled Regional split #1", factor=factor_2)
    pc2.plot()

    sample_3 = {
        "Europe": 45,
        "Developed Markets": 10,
        "Emerging Markets": 45,
    }
    factor_3 = {
        "value": 100000,
        "unit": "USD",
    }
    pc3 = PieChart(data=sample_3, title="Labeled Regional split #2", factor=factor_3)
    pc3.plot()

    pc4 = pc2 + pc3
    pc4.plot()
    plt.show()