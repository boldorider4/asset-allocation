from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .chart_merge import PieFactor, merge_charts
from .visual import Visual

# matplotlib tab10 — explicit slice colors for the JS engine
_TAB10 = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")

logger = logging.getLogger(__name__)


def _slug(title: str | None) -> str:
    if not title:
        return "chart"
    slug = _NON_SLUG.sub("-", title.lower()).strip("-")
    return slug or "chart"


class WebChart(Visual):
    """Write one JSON ``*.raw`` pie payload for the ``_visualizer`` JS app."""

    data_dir: Path = Path("_visualizer") / "data"
    _slug_counts: dict[str, int] = {}

    def __init__(
        self,
        data: dict[str, float],
        title: str | None = None,
        *,
        factor: PieFactor | None = None,
    ):
        super().__init__(data=data, title=title)
        self._factor = factor

    def __add__(self, other: object) -> WebChart:
        if not isinstance(other, WebChart):
            return NotImplemented
        merged, title, factor = merge_charts(
            self._data,
            self._title,
            self._factor,
            other._data,
            other._title,
            other._factor,
        )
        return WebChart(data=merged, title=title, factor=factor)

    def _payload(self) -> dict:
        wedges = [
            {
                "label": label,
                "weight": float(weight),
                "color": _TAB10[i % len(_TAB10)],
            }
            for i, (label, weight) in enumerate(self._data.items())
        ]
        payload: dict = {
            "name": self._title or "Untitled",
            "wedges": wedges,
        }
        if self._factor is not None:
            payload["factor"] = dict(self._factor)
        return payload

    def _unique_stem(self, base: str) -> str:
        n = type(self)._slug_counts.get(base, 0)
        type(self)._slug_counts[base] = n + 1
        if n == 0:
            return base
        return f"{base}-{n + 1}"

    def plot(self, **_kwargs) -> None:
        if not self._data:
            raise ValueError("data must contain at least one entry")
        sizes = [float(v) for v in self._data.values()]
        if any(s < 0 for s in sizes):
            raise ValueError("weights must be non-negative")
        if sum(sizes) <= 0:
            raise ValueError("sum of weights must be positive")

        dest = type(self).data_dir
        dest.mkdir(parents=True, exist_ok=True)
        stem = self._unique_stem(_slug(self._title))
        path = dest / f"{stem}.raw"
        path.write_text(json.dumps(self._payload(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def write_example(cls) -> None:
        """Write a couple of sample ``*.raw`` files into :attr:`data_dir`."""
        cls._slug_counts = {}
        examples = (
            WebChart(
                data={
                    "US": 0.42,
                    "Ex-US": 0.38,
                    "Emerging Markets": 0.20,
                },
                title="Example: Regional split",
                factor={"value": 125000, "unit": "Euro"},
            ),
            WebChart(
                data={
                    "Equity": 0.55,
                    "Bonds": 0.25,
                    "Commodities": 0.10,
                    "Cash": 0.10,
                },
                title="Example: Asset mix",
                factor={"value": 250000, "unit": "Euro"},
            ),
        )
        for chart in examples:
            chart.plot()

    @classmethod
    def finish_plots(cls) -> None:
        logger.info("Wrote chart data to %s", cls.data_dir)
