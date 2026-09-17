from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Sequence

from .chart_merge import PieFactor, merge_charts, merge_closing_title
from .visual_window import VisualWindow

import matplotlib.pyplot as plt


def _group_thousands(n: float, *, decimals: int = 2) -> str:
    """Format a float with an apostrophe every three digits before the decimal (e.g. 1'000'000.00)."""
    return format(n, f",.{decimals}f").replace(",", "'")


# Decimal literals in titles (e.g. from "{:.2f}"); ``(?!\.\d)`` avoids matching the first octets of "192.168.0.1".
_TITLE_FLOAT = re.compile(r"-?\d+\.\d+(?!\.\d)")


def _title_with_grouped_floats(s: str) -> str:
    """Replace each decimal float substring in *s* with ``_group_thousands`` (preserving fractional width)."""

    def repl(m: re.Match[str]) -> str:
        raw = m.group(0)
        frac = raw.partition(".")[2]
        return _group_thousands(float(raw), decimals=len(frac))

    return _TITLE_FLOAT.sub(repl, s)


class PieChart(VisualWindow):
    """Pie chart from label → weight; slice areas match each weight’s share of the total."""

    def __init__(
        self,
        data: dict[str, float],
        title: str | None = None,
        *,
        closing_title: str | None = None,
        factor: PieFactor | None = None,
    ):
        super().__init__(data=data, title=title, closing_title=closing_title)
        self._factor = factor

    def __add__(self, other: object) -> PieChart:
        if not isinstance(other, PieChart):
            return NotImplemented
        merged, title, factor = merge_charts(
            self._data,
            self._title,
            self._factor,
            other._data,
            other._title,
            other._factor,
        )
        closing = merge_closing_title(self._closing_title, other._closing_title, factor)
        return PieChart(data=merged, title=title, closing_title=closing, factor=factor)

    def plot(
        self,
        *,
        label_fontsize: float | None = None,
        autopct_fontsize: float | None = None,
        colors: Sequence[str] | None = None,
    ) -> None:
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
                return f"{pct:.1f}%\n{_group_thousands(portion)} {unit}"

            autopct_arg = autopct
        else:
            autopct_arg = "%1.1f%%"

        _wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            autopct=autopct_arg,
            startangle=90,
            **({"colors": list(colors)} if colors is not None else {}),
        )
        if label_fontsize is not None:
            for t in texts:
                t.set_fontsize(label_fontsize)
        if autopct_fontsize is not None:
            for t in autotexts:
                t.set_fontsize(autopct_fontsize)
        ax.axis("equal")
        display_title = _title_with_grouped_floats(self._title) if self._title is not None else None
        display_closing = (
            _title_with_grouped_floats(self._closing_title) if self._closing_title is not None else None
        )
        if display_title is not None:
            fig.suptitle(display_title)
        if display_closing is not None:
            fig.text(
                0.5,
                0.04,
                display_closing,
                ha="center",
                va="center",
                fontsize=11,
                fontweight="bold",
            )
            top = 0.88 if display_title is not None else 0.95
            fig.subplots_adjust(bottom=0.12, top=top)
        # Non-blocking so multiple charts each get their own window.
        plt.show(block=False)
        # Position/size after show so the native window exists (stagger is no-op otherwise).
        self._stagger_figure_window(fig)
        if display_title is not None:
            mgr = fig.canvas.manager
            if mgr is not None:
                setter = getattr(mgr, "set_window_title", None)
                if callable(setter):
                    try:
                        setter(display_title)
                    except Exception:
                        pass
        plt.pause(0.02)


if __name__ == "__main__":
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
