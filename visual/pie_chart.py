from __future__ import annotations

from typing import TypedDict

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
        if factor is not None and ("value" not in factor or "unit" not in factor):
            raise ValueError("factor must contain both 'value' and 'unit' keys")
        self._factor = factor

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

        fig_kw: dict[str, str] = {"num": self._title} if self._title is not None else {}
        fig, ax = plt.subplots(**fig_kw)

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
        fig.tight_layout()
        self._stagger_figure_window(fig)
        # Non-blocking so multiple charts each get their own window.
        plt.show(block=False)
        plt.pause(0.01)


if __name__ == "__main__":
    sample = {
        "USA": 45,
        "Emerging Markets": 23,
        "ex-USA-Developed": 22,
    }
    pc0 = PieChart(data=sample, title="Regional split")
    pc0.plot()
    sample = {
        "Europe": 45,
        "Developed Markets": 23,
        "Emerging Markets": 11,
    }
    pc1 = PieChart(data=sample, title="Other Regional split")
    pc1.plot()
    sample = {
        "Europe": 45,
        "Developed Markets": 23,
        "Emerging Markets": 12,
    }
    factor = {
        "value": 100000,
        "unit": "USD",
    }
    pc2 = PieChart(data=sample, title="Labeled Regional split", factor=factor)
    pc2.plot()
    plt.show()