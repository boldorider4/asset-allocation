from .visual import SECTOR_PALETTE, Visual
from .web_chart import WebChart
from .pie_chart import PieChart

PLOTTERS = {
    "web": WebChart,
    "pie-chart": PieChart,
}

# Back-compat alias (same keys as RuntimeContext plotter names).
PLOT_CHOICES = PLOTTERS


def plotter_class(kind: str):
    """Resolve a plotter name to its chart class (no module-global selection)."""
    try:
        return PLOTTERS[kind]
    except KeyError as exc:
        raise ValueError(f"unknown plotter {kind!r}") from exc


__all__ = [
    "Visual",
    "SECTOR_PALETTE",
    "WebChart",
    "PieChart",
    "PLOTTERS",
    "PLOT_CHOICES",
    "plotter_class",
]
