from .visual import Visual
from .web_chart import WebChart
from .pie_chart import PieChart

DEFAULT_VISUALIZER = PieChart

__all__ = ["Visual", "WebChart", "PieChart", "DEFAULT_VISUALIZER"]
