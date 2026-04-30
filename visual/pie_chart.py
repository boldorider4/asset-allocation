from __future__ import annotations

import matplotlib.pyplot as plt

from visual import Visual


class PieChart(Visual):
    """Pie chart from label → weight; slice areas match each weight’s share of the total."""

    def __init__(self, data: dict[str, float], title: str | None = None):
        super().__init__(data=data, title=title)

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
        # Non-blocking so multiple charts each get their own window.
        plt.show(block=False)
        plt.pause(0.01)


if __name__ == "__main__":
    sample = {
        "USA": 45,
        "Emerging Markets": 23,
        "ex-USA-Developed": 12,
    }
    pc0 = PieChart(data=sample, title="Regional split")
    pc0.plot()
    sample = {
        "Europe": 45,
        "Developed Markets": 23,
        "Emerging Markets": 12,
    }
    pc1 = PieChart(data=sample, title="Othr Regional split")
    pc1.plot()