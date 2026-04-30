from __future__ import annotations

import matplotlib.pyplot as plt

from visual import Visual


class PieChart(Visual):
    """Pie chart from label → weight; slice areas match each weight’s share of the total."""

    def __init__(self, data: dict[str, float], title: str | None = None):
        self._data = data
        self._title = title

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
        ax.pie(
            sizes,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
        )
        ax.axis("equal")
        if self._title is not None:
            fig.suptitle(self._title)
        fig.tight_layout()
        plt.show()


if __name__ == "__main__":
    sample = {
        "USA": 45,
        "Emerging Markets": 23,
        "ex-USA-Developed": 12,
    }
    PieChart(data=sample, title="Regional split").plot()
