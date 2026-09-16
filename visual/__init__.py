from .visual import SECTOR_PALETTE, Visual
from .web_chart import WebChart
from .pie_chart import PieChart

PLOT_CHOICES = {
    "web": WebChart,
    "pie-chart": PieChart,
}

DEFAULT_VISUALIZER = WebChart


def set_plotter(kind: str) -> None:
    """Select the default chart class (`web` or `pie-chart`)."""
    global DEFAULT_VISUALIZER
    try:
        DEFAULT_VISUALIZER = PLOT_CHOICES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown plotter {kind!r}") from exc


def get_plotter():
    return DEFAULT_VISUALIZER


__all__ = [
    "Visual",
    "SECTOR_PALETTE",
    "WebChart",
    "PieChart",
    "DEFAULT_VISUALIZER",
    "PLOT_CHOICES",
    "set_plotter",
    "get_plotter",
]
