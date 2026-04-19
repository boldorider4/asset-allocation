from __future__ import annotations

import matplotlib.pyplot as plt

from visual import Visual


class PieChart(Visual):
    """Pie chart from label → weight; slice areas match each weight’s share of the total."""

    def plot(self, data: dict[str, float], *, title: str | None = None) -> None:
        if not data:
            raise ValueError("data must contain at least one entry")

        labels = list(data.keys())
        sizes = [float(data[k]) for k in labels]

        if any(s < 0 for s in sizes):
            raise ValueError("weights must be non-negative")

        total = sum(sizes)
        if total <= 0:
            raise ValueError("sum of weights must be positive")

        fig_kw: dict[str, str] = {"num": title} if title is not None else {}
        fig, ax = plt.subplots(**fig_kw)
        ax.pie(
            sizes,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
        )
        ax.axis("equal")
        if title is not None:
            fig.suptitle(title)
        fig.tight_layout()
        plt.show()


if __name__ == "__main__":
    sample = {
        "USA": 45,
        "Emerging Markets": 23,
        "ex-USA-Developed": 12,
    }
    PieChart().plot(sample, title="Regional split")
